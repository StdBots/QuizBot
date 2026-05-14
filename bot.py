"""
ExactQuizBot — replicates original QuizBot flow exactly.
Images reference flow:
  /start → Create New Quiz → title → description(/skip) → poll button → questions
  → /done → timer → shuffle → quiz card (Start / Group / Share / Edit / Stats)
  → Play: I am ready! → quiz polls → results with speed leaderboard
"""

import asyncio
import copy
import logging
import random
import time
from collections import defaultdict

from telegram import (
    ReplyKeyboardRemove,
    Update,
    Poll,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    PollAnswerHandler,
    filters,
)

from config import TELEGRAM_TOKEN
from database import (
    create_quiz, get_quiz, get_user_quizzes, update_quiz,
    push_question, pop_last_question, delete_quiz,
    delete_question_by_index, save_player_score, get_leaderboard,
)
from keyboards import (
    main_menu_kb, quiz_card_kb, timer_kb, shuffle_kb,
    ready_kb, edit_quiz_kb, remove_questions_kb,
    my_quizzes_kb, share_quiz_kb, create_question_poll_kb,
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Per-chat async lock
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

MAIN_TEXT = (
    "This bot will help you *create a quiz* with a series of *multiple choice questions*."
)


# ═══════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_type = update.effective_chat.type

    # GROUP: bot added with deeplink  ?startgroup=<quiz_id>
    if chat_type in ("group", "supergroup"):
        if args:
            quiz_id = args[0]
            await _group_lobby(update, context, quiz_id)
        else:
            await update.message.reply_text(
                "👋 Add me to a group via the *Start quiz in group* button from your quiz in private chat.",
                parse_mode="Markdown",
            )
        return

    # PRIVATE: someone opened a shared quiz link  ?start=<quiz_id>
    if args:
        quiz_id = args[0]
        quiz = get_quiz(quiz_id)
        if quiz:
            await _show_quiz_card(update, context, quiz_id, is_msg=True)
            return

    # Normal /start
    context.user_data.pop("state", None)
    await update.message.reply_text(
        MAIN_TEXT,
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )


# ═══════════════════════════════════════════════════════════
#  /stop  — cancel running quiz
# ═══════════════════════════════════════════════════════════

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    src = context.chat_data if update.effective_chat.type in ("group","supergroup") else context.user_data
    if src.get("quiz_running"):
        src["quiz_aborted"] = True
        await update.message.reply_text("⛔️ Quiz stopped.")
    else:
        await update.message.reply_text("No quiz is running right now.")


# ═══════════════════════════════════════════════════════════
#  /skip  — skip description step
# ═══════════════════════════════════════════════════════════

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if state and state.get("step") == "description":
        state["step"] = "questions"
        await update.message.reply_text(
            "Good. Now send me a *poll* with your first question.\n\n"
            "⚠️ Make sure *Quiz Mode* is ON in the poll creator.",
            parse_mode="Markdown",
            reply_markup=create_question_poll_kb(),
        )
    else:
        await update.message.reply_text("Nothing to skip right now.")


# ═══════════════════════════════════════════════════════════
#  /done  — finish adding questions
# ═══════════════════════════════════════════════════════════

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if not state or state.get("step") != "questions":
        await update.message.reply_text("You are not creating a quiz right now.")
        return

    quiz_id = state["quiz_id"]
    quiz = get_quiz(quiz_id)
    if not quiz or not quiz.get("questions"):
        await update.message.reply_text("❌ Add at least 1 question before finishing!")
        return

    context.user_data.pop("state", None)
    await update.message.reply_text(
        "Please set a *time limit* for questions. In groups, the bot will send the "
        "next question as soon as this time is up.\n\n"
        "_We recommend using longer timers only if your quiz involves complex problems "
        "(like math, etc.). For most trivia-like quizzes, 10-30 seconds are more than enough._",
        parse_mode="Markdown",
        reply_markup=timer_kb(quiz_id, context="setup"),
    )


# ═══════════════════════════════════════════════════════════
#  /undo  — remove last question
# ═══════════════════════════════════════════════════════════

async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if not state or state.get("step") != "questions":
        await update.message.reply_text("Nothing to undo.")
        return
    quiz_id = state["quiz_id"]
    if pop_last_question(quiz_id):
        quiz = get_quiz(quiz_id)
        n = len(quiz.get("questions", []))
        await update.message.reply_text(
            f"↩️ Last question removed. Your quiz now has *{n}* question(s).\n\n"
            "Send the next question or /done to finish.",
            parse_mode="Markdown",
            reply_markup=create_question_poll_kb(),
        )
    else:
        await update.message.reply_text("No questions to undo.")


# ═══════════════════════════════════════════════════════════
#  TEXT HANDLER  (title / description / rename)
# ═══════════════════════════════════════════════════════════

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if not state:
        return
    text = update.message.text.strip()
    step = state.get("step")

    if step == "title":
        quiz_id = create_quiz(update.effective_user.id, text)
        state["quiz_id"] = quiz_id
        state["step"] = "description"
        await update.message.reply_text(
            "Good. Now send me a *description* of your quiz. This is optional, you can /skip this step.",
            parse_mode="Markdown",
        )

    elif step == "description":
        update_quiz(state["quiz_id"], {"description": text})
        state["step"] = "questions"
        await update.message.reply_text(
            "Good. Now send me a *poll* with your first question. Alternatively, "
            "you can send a message with *text or media* that will be shown *before* this question.\n\n"
            "⚠️ *Warning:* this bot can't create anonymous polls. "
            "Users in groups will see votes from other members.",
            parse_mode="Markdown",
            reply_markup=create_question_poll_kb(),
        )

    elif step == "rename":
        update_quiz(state["quiz_id"], {"title": text})
        quiz_id = state["quiz_id"]
        context.user_data.pop("state", None)
        await update.message.reply_text(
            f"✅ Quiz renamed to *{text}*!",
            parse_mode="Markdown",
        )
        await _show_quiz_card(update, context, quiz_id, is_msg=True)

    elif step == "questions":
        # Text/media shown before next poll — just confirm and keep waiting
        await update.message.reply_text(
            "✅ Text saved. Now send the *poll* for this question.",
            parse_mode="Markdown",
            reply_markup=create_question_poll_kb(),
        )


# ═══════════════════════════════════════════════════════════
#  POLL HANDLER  (user sends a quiz poll while creating)
# ═══════════════════════════════════════════════════════════

async def poll_create_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if not state or state.get("step") != "questions":
        return

    poll = update.message.poll
    if poll.type != "quiz":
        await update.message.reply_text("⚠️ Please send a *Quiz* type poll (not a regular poll).", parse_mode="Markdown")
        return

    quiz_id = state["quiz_id"]
    q = {
        "question":      poll.question,
        "options":       [o.text for o in poll.options],
        "correct_index": poll.correct_option_id,
        "explanation":   poll.explanation or "",
    }
    push_question(quiz_id, q)
    quiz = get_quiz(quiz_id)
    n = len(quiz["questions"])

    await update.message.reply_text(
        f"Good. Your quiz *'{quiz['title']}'* now has *{n}* question(s). "
        f"If you made a mistake in the question, you can go back by sending /undo.\n\n"
        f"Now send the next question – or some text or media that will be shown before it.\n\n"
        f"When done, simply send /done to finish creating the quiz.",
        parse_mode="Markdown",
        reply_markup=create_question_poll_kb(),
    )


# ═══════════════════════════════════════════════════════════
#  BUTTON HANDLER
# ═══════════════════════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # ── Main menu ──────────────────────────────────────────
    if data == "main_menu":
        context.user_data.pop("state", None)
        await query.message.edit_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())

    elif data == "create_quiz":
        context.user_data["state"] = {"step": "title"}
        await query.message.reply_text(
            "Let's create a new quiz. First, send me the *title* of your quiz "
            "(e.g., 'Aptitude Test' or '10 questions about bears').",
            parse_mode="Markdown",
        )

    elif data == "my_quizzes":
        quizzes = get_user_quizzes(user_id)
        if not quizzes:
            await query.message.edit_text(
                "You have no saved quizzes.\n\nCreate one first!",
                reply_markup=InlineKeyboardMarkup_back(),
            )
            return
        await query.message.edit_text(
            "📋 *Your quizzes:*", parse_mode="Markdown",
            reply_markup=my_quizzes_kb(quizzes),
        )

    # ── Quiz card ──────────────────────────────────────────
    elif data.startswith("quiz_card_"):
        quiz_id = data.replace("quiz_card_", "")
        await _show_quiz_card(update, context, quiz_id, is_msg=False)

    # ── Start DM (I am ready in private) ──────────────────
    elif data.startswith("start_dm_"):
        quiz_id = data.replace("start_dm_", "")
        quiz = get_quiz(quiz_id)
        if not quiz or not quiz.get("questions"):
            await query.answer("❌ Quiz has no questions!", show_alert=True)
            return
        await _show_ready_screen(query, context, quiz_id, is_group=False)

    # ── Timer (setup after /done) ──────────────────────────
    elif data.startswith("timer_setup_"):
        # timer_setup_{quiz_id}_{seconds}
        parts = data.replace("timer_setup_", "").rsplit("_", 1)
        quiz_id, seconds = parts[0], int(parts[1])
        update_quiz(quiz_id, {"timer": seconds})
        await query.message.edit_text(
            "Shuffle questions and answer options?",
            reply_markup=shuffle_kb(quiz_id, context="setup"),
        )

    # ── Shuffle (setup) ────────────────────────────────────
    elif data.startswith("shuffle_setup_"):
        # shuffle_setup_{quiz_id}_{mode}
        parts = data.replace("shuffle_setup_", "").rsplit("_", 1)
        quiz_id, mode = parts[0], parts[1]
        update_quiz(quiz_id, {"shuffle": mode})
        # Show quiz card
        await query.message.edit_text("👍 *Quiz created.*", parse_mode="Markdown")
        await _show_quiz_card(update, context, quiz_id, is_msg=True, after_query=query)

    # ── Timer (dm play) ───────────────────────────────────
    elif data.startswith("timer_dm_"):
        parts = data.replace("timer_dm_", "").rsplit("_", 1)
        quiz_id, seconds = parts[0], int(parts[1])
        context.user_data["play_timer"] = seconds
        await query.message.edit_text(
            "Shuffle questions and answer options?",
            reply_markup=shuffle_kb(quiz_id, context="dm"),
        )

    # ── Shuffle (dm play) ─────────────────────────────────
    elif data.startswith("shuffle_dm_"):
        parts = data.replace("shuffle_dm_", "").rsplit("_", 1)
        quiz_id, mode = parts[0], parts[1]
        timer = context.user_data.get("play_timer", 30)
        chat_id = query.message.chat_id
        await query.message.delete()
        asyncio.create_task(
            _run_quiz(context, chat_id, user_id, quiz_id, timer, mode, is_group=False)
        )

    # ── Timer (group play from lobby) ─────────────────────
    elif data.startswith("timer_grp_"):
        parts = data.replace("timer_grp_", "").rsplit("_", 1)
        quiz_id, seconds = parts[0], int(parts[1])
        context.chat_data["play_timer"] = seconds
        await query.message.edit_text(
            "Shuffle questions and answer options?",
            reply_markup=shuffle_kb(quiz_id, context="grp"),
        )

    # ── Shuffle (group play) ──────────────────────────────
    elif data.startswith("shuffle_grp_"):
        parts = data.replace("shuffle_grp_", "").rsplit("_", 1)
        quiz_id, mode = parts[0], parts[1]
        timer = context.chat_data.get("play_timer", 30)
        chat_id = query.message.chat_id
        await query.message.delete()
        asyncio.create_task(
            _run_quiz(context, chat_id, query.from_user.id, quiz_id, timer, mode, is_group=True)
        )

    # ── I am ready! (group lobby) ─────────────────────────
    elif data.startswith("ready_"):
        await _ready_button(update, context)

    # ── Edit quiz ─────────────────────────────────────────
    elif data.startswith("edit_add_"):
        quiz_id = data.replace("edit_add_", "")
        context.user_data["state"] = {"step": "questions", "quiz_id": quiz_id}
        await query.message.reply_text(
            "Send me the next question poll:",
            reply_markup=create_question_poll_kb(),
        )

    elif data.startswith("edit_remove_"):
        quiz_id = data.replace("edit_remove_", "")
        quiz = get_quiz(quiz_id)
        qs = quiz.get("questions", [])
        if not qs:
            await query.answer("No questions to remove!", show_alert=True)
            return
        await query.message.edit_text(
            "Select question to remove:",
            reply_markup=remove_questions_kb(quiz_id, qs),
        )

    elif data.startswith("edit_rename_"):
        quiz_id = data.replace("edit_rename_", "")
        context.user_data["state"] = {"step": "rename", "quiz_id": quiz_id}
        await query.message.reply_text("Send me the new name for your quiz:")

    elif data.startswith("edit_"):
        quiz_id = data.replace("edit_", "")
        quiz = get_quiz(quiz_id)
        if not quiz:
            await query.answer("Quiz not found!", show_alert=True)
            return
        await query.message.edit_text(
            f"✏️ Editing *{quiz['title']}*\n({len(quiz.get('questions',[]))} questions)",
            parse_mode="Markdown",
            reply_markup=edit_quiz_kb(quiz_id),
        )

    elif data.startswith("rmq_"):
        # rmq_{quiz_id}_{index}
        parts = data.replace("rmq_", "").rsplit("_", 1)
        quiz_id, idx = parts[0], int(parts[1])
        delete_question_by_index(quiz_id, idx)
        quiz = get_quiz(quiz_id)
        await query.message.edit_text(
            f"✅ Question removed. *{quiz['title']}* now has {len(quiz.get('questions',[]))} questions.",
            parse_mode="Markdown",
            reply_markup=edit_quiz_kb(quiz_id),
        )

    # ── Stats ─────────────────────────────────────────────
    elif data.startswith("stats_"):
        quiz_id = data.replace("stats_", "")
        quiz = get_quiz(quiz_id)
        scores = get_leaderboard(quiz_id, 0)  # global (chat_id=0 means all)
        if not scores:
            await query.answer("No stats yet for this quiz.", show_alert=True)
            return
        lines = [f"📊 *Stats for '{quiz['title']}'*\n"]
        medals = ["🥇","🥈","🥉"]
        for i, s in enumerate(scores):
            m = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{m} @{s['username']} — {s['correct']}/{s['total']} ({s['total_time']}s)")
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")


def InlineKeyboardMarkup_back():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Back", callback_data="main_menu")]])


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

async def _show_quiz_card(update, context, quiz_id, is_msg=True, after_query=None):
    quiz = get_quiz(quiz_id)
    if not quiz:
        return
    bot_username = (await context.bot.get_me()).username
    n_q = len(quiz.get("questions", []))
    timer = quiz.get("timer", "?")
    shuffle = quiz.get("shuffle", "none")
    timer_str = f"{timer}s" if isinstance(timer, int) and timer < 60 else (f"{timer//60} min" if isinstance(timer, int) else str(timer))

    text = (
        f"*{quiz['title']}*"
        + (f"\n_{quiz['description']}_" if quiz.get("description") else "") +
        f"\n\n"
        f"✏️ {n_q} question{'s' if n_q != 1 else ''} · "
        f"⏱ {timer_str} · "
        f"🔀 {shuffle}\n\n"
        f"External sharing link:\n"
        f"t.me/{bot_username}?start={quiz_id}"
    )
    kb = quiz_card_kb(quiz_id, bot_username)

    if after_query:
        await after_query.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    elif is_msg:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)


async def _show_ready_screen(query_or_msg, context, quiz_id, is_group: bool):
    """Show the 'Get ready' message with I am ready! button."""
    quiz = get_quiz(quiz_id)
    n_q = len(quiz.get("questions", []))
    timer = quiz.get("timer", 30)
    timer_str = f"{timer} seconds" if timer < 60 else f"{timer//60} minute(s)"
    shuffle = quiz.get("shuffle", "none")

    text = (
        f"♟ Get ready for the quiz *'{quiz['title']}'*\n\n"
        f"_{quiz.get('description','')}_\n\n" if quiz.get("description") else
        f"♟ Get ready for the quiz *'{quiz['title']}'*\n\n"
    ) + (
        f"✏️ {n_q} question{'s' if n_q!=1 else ''}\n"
        f"⏱ {timer_str} per question\n"
        f"👁 Votes are *visible* to {'group members and the' if is_group else ''} quiz owner\n\n"
        + ("♟ The quiz will begin when at least 2 people are ready to play.\n"
           "Send /stop to stop it." if is_group else
           "♟ Press the button below when you are ready.\nSend /stop to stop it.")
    )

    msg = query_or_msg.message if hasattr(query_or_msg, "message") else query_or_msg
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=ready_kb(quiz_id))


async def _group_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_id: str):
    """Called when bot is added to group with ?startgroup=<quiz_id>."""
    quiz = get_quiz(quiz_id)
    if not quiz:
        await update.message.reply_text("❌ Quiz not found.")
        return

    context.chat_data["lobby_quiz_id"] = quiz_id
    context.chat_data["ready_users"] = {}   # {user_id: (name, join_time)}
    context.chat_data["quiz_running"] = False

    n_q = len(quiz.get("questions", []))
    timer = quiz.get("timer", 30)
    timer_str = f"{timer} seconds" if timer < 60 else f"{timer//60} minute(s)"

    text = (
        f"♟ Get ready for the quiz *'{quiz['title']}'*\n\n"
        + (f"_{quiz['description']}_\n\n" if quiz.get("description") else "") +
        f"✏️ {n_q} question{'s' if n_q!=1 else ''}\n"
        f"⏱ {timer_str} per question\n"
        f"👁 Votes are *visible* to group members and the quiz owner\n\n"
        f"♟ The quiz will begin when at least *2 people* are ready to play.\n"
        f"Send /stop to stop it."
    )
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=ready_kb(quiz_id),
    )


async def _ready_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    quiz_id = query.data.replace("ready_", "")
    chat_id = query.message.chat_id
    user = query.from_user
    is_group = query.message.chat.type in ("group", "supergroup")

    if is_group:
        # ── GROUP LOBBY ───────────────────────────────────
        async with _locks[chat_id]:
            if context.chat_data.get("quiz_running"):
                await query.answer("Quiz is already running!", show_alert=False)
                return

            ready = context.chat_data.setdefault("ready_users", {})
            if user.id in ready:
                await query.answer("You're already ready! ✅", show_alert=False)
                return

            ready[user.id] = (user.first_name, time.time())
            count = len(ready)
            await query.answer(f"✅ You're ready! ({count} player{'s' if count>1 else ''})", show_alert=False)

            # Update button count
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                await query.message.edit_reply_markup(
                    InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"{count} ✅ I am ready!", callback_data=f"ready_{quiz_id}")
                    ]])
                )
            except Exception:
                pass

            if count >= 2:
                context.chat_data["quiz_running"] = True
                await query.message.delete()
                # Ask timer (already saved from creation, but let admin choose again)
                quiz = get_quiz(quiz_id)
                await context.bot.send_message(
                    chat_id,
                    "⏱ Please set a *time limit* for questions:",
                    parse_mode="Markdown",
                    reply_markup=timer_kb(quiz_id, context="grp"),
                )
    else:
        # ── PRIVATE: single player, just start ────────────
        await query.answer()
        await query.message.delete()
        quiz = get_quiz(quiz_id)
        timer = quiz.get("timer", 30)
        shuffle = quiz.get("shuffle", "none")
        asyncio.create_task(
            _run_quiz(context, chat_id, user.id, quiz_id, timer, shuffle, is_group=False)
        )


# ═══════════════════════════════════════════════════════════
#  QUIZ RUNNER
# ═══════════════════════════════════════════════════════════

async def _run_quiz(context, chat_id, user_id, quiz_id, timer, shuffle_mode, is_group):
    quiz = get_quiz(quiz_id)
    questions = copy.deepcopy(quiz["questions"])

    if shuffle_mode in ("all", "questions"):
        random.shuffle(questions)

    src = context.application.chat_data.setdefault(chat_id, {}) if is_group \
        else context.application.user_data.setdefault(user_id, {})

    async with _locks[chat_id]:
        src["quiz_running"]  = True
        src["quiz_aborted"]  = False
        src["correct_map"]   = {}   # {q_idx: correct_option_id}
        src["player_scores"] = {}   # {user_id: {name, correct, wrong, answer_times:[]}}

    if "poll_map" not in context.bot_data:
        context.bot_data["poll_map"] = {}

    total = len(questions)

    for idx, q in enumerate(questions, start=1):
        async with _locks[chat_id]:
            if src.get("quiz_aborted"):
                break

        options = list(q["options"])
        correct_idx = q["correct_index"]

        if shuffle_mode in ("all", "answers"):
            paired = list(enumerate(options))
            random.shuffle(paired)
            old_idx, new_opts = zip(*paired)
            options = list(new_opts)
            correct_idx = list(old_idx).index(q["correct_index"])

        async with _locks[chat_id]:
            src["correct_map"][idx] = correct_idx

        try:
            msg = await context.bot.send_poll(
                chat_id=chat_id,
                question=f"[{idx}/{total}] {q['question']}",
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                explanation=q.get("explanation", "") or None,
                open_period=timer,
                is_anonymous=False,
            )
            context.bot_data["poll_map"][msg.poll.id] = {
                "chat_id": chat_id,
                "user_id": user_id,
                "is_group": is_group,
                "q_idx": idx,
                "sent_at": time.time(),
            }
        except Exception as e:
            logger.error(f"Poll send error Q{idx}: {e}")

        await asyncio.sleep(timer + 1.5)

    # Send results
    await asyncio.sleep(2)
    await _send_results(context, chat_id, user_id, quiz_id, quiz["title"], total, is_group)


# ═══════════════════════════════════════════════════════════
#  POLL ANSWER HANDLER
# ═══════════════════════════════════════════════════════════

async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_id = update.poll_answer.poll_id
    poll_map = context.bot_data.get("poll_map", {})
    info = poll_map.get(poll_id)
    if not info or not update.poll_answer.option_ids:
        return

    chat_id   = info["chat_id"]
    is_group  = info["is_group"]
    q_idx     = info["q_idx"]
    sent_at   = info["sent_at"]
    user      = update.poll_answer.user
    answer_time = round(time.time() - sent_at, 1)

    async with _locks[chat_id]:
        src_uid = info["user_id"]
        src = context.application.chat_data.get(chat_id) if is_group \
            else context.application.user_data.get(src_uid)
        if not src:
            return

        correct_idx = src.get("correct_map", {}).get(q_idx)
        is_correct  = update.poll_answer.option_ids[0] == correct_idx

        scores = src.setdefault("player_scores", {})
        pid = str(user.id)
        if pid not in scores:
            scores[pid] = {
                "name": user.username or user.first_name,
                "first_name": user.first_name,
                "correct": 0,
                "wrong": 0,
                "answer_times": [],
            }
        if is_correct:
            scores[pid]["correct"] += 1
            scores[pid]["answer_times"].append(answer_time)
        else:
            scores[pid]["wrong"] += 1


# ═══════════════════════════════════════════════════════════
#  RESULTS
# ═══════════════════════════════════════════════════════════

async def _send_results(context, chat_id, user_id, quiz_id, quiz_title, total, is_group):
    bot_username = (await context.bot.get_me()).username
    src = context.application.chat_data.get(chat_id) if is_group \
        else context.application.user_data.get(user_id)
    scores = (src or {}).get("player_scores", {})

    if is_group:
        sorted_players = sorted(
            scores.values(),
            key=lambda p: (-p["correct"], sum(p["answer_times"]) if p["answer_times"] else 9999)
        )

        if not sorted_players:
            text = f"♟ The quiz *'{quiz_title}'* has finished!\n\nNobody answered."
        else:
            lines = [f"♟ The quiz *'{quiz_title}'* has finished!\n"]
            n_answered = sum(p["correct"] + p["wrong"] for p in sorted_players)
            lines.append(f"*{n_answered} question{'s' if n_answered!=1 else ''} answered*\n")
            medals = ["🥇","🥈","🥉"]
            for i, p in enumerate(sorted_players[:10]):
                m = medals[i] if i < 3 else f"{i+1}."
                avg_t = round(sum(p["answer_times"]) / len(p["answer_times"]), 1) if p["answer_times"] else 0
                lines.append(f"{m} @{p['name']} – {p['correct']} ({avg_t} sec)")
                save_player_score(quiz_id, chat_id, 0, p["name"], p["correct"], total, avg_t)

            lines.append("\n🏆 *Congratulations to the winners!*")
            text = "\n".join(lines)

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔗 Share quiz", url=f"https://t.me/{bot_username}?start={quiz_id}")
        ]])
        await context.bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)

    else:
        # Private — show personal score
        my = scores.get(str(user_id), {"correct": 0, "wrong": 0, "answer_times": []})
        correct  = my["correct"]
        wrong    = my["wrong"]
        missed   = total - correct - wrong
        avg_t    = round(sum(my["answer_times"]) / len(my["answer_times"]), 1) if my["answer_times"] else 0

        text = (
            f"♟ The quiz *'{quiz_title}'* has finished!\n\n"
            f"You answered *{correct + wrong}* question{'s' if correct+wrong!=1 else ''}:\n\n"
            f"✅ Correct – {correct}\n"
            f"❌ Wrong – {wrong}\n"
            f"⌛ Missed – {missed}\n"
            f"⏱ {avg_t} sec avg"
        )
        save_player_score(quiz_id, chat_id, user_id, "You", correct, total, avg_t)

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔗 Share quiz", url=f"https://t.me/{bot_username}?start={quiz_id}")
        ]])
        await context.bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)

    # Cleanup
    async with _locks[chat_id]:
        if src:
            for k in ("quiz_running","quiz_aborted","correct_map","player_scores",
                      "ready_users","lobby_quiz_id","play_timer"):
                src.pop(k, None)

    pm = context.bot_data.get("poll_map", {})
    stale = [k for k, v in pm.items() if v.get("chat_id") == chat_id]
    for k in stale:
        pm.pop(k, None)


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("Set TELEGRAM_TOKEN in config.py or as env variable!")

    persistence = PicklePersistence(filepath="quizbot_state")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("done",  cmd_done))
    app.add_handler(CommandHandler("skip",  cmd_skip))
    app.add_handler(CommandHandler("undo",  cmd_undo))
    app.add_handler(CommandHandler("stop",  cmd_stop))

    # Poll creation (user forwards quiz poll)
    app.add_handler(MessageHandler(filters.POLL, poll_create_handler))

    # Text (title / description / rename)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # All inline buttons
    app.add_handler(CallbackQueryHandler(button_handler))

    # Poll answers during quiz
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    logger.info("🤖 ExactQuizBot running...")
    app.run_polling(allowed_updates=["message", "callback_query", "poll_answer"])


if __name__ == "__main__":
    main()
