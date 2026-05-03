import asyncio
import copy
import random
import logging
import time
from collections import defaultdict
from pymongo import MongoClient
from bson import ObjectId
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)
# Make sure you have a config.py file with your bot token and MongoDB URI
from config import TELEGRAM_TOKEN, MONGO_URI, DB_NAME

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- LOCKS FOR ASYNC SAFETY ---
user_locks = defaultdict(asyncio.Lock)
group_locks = defaultdict(asyncio.Lock)

# -------------------- MONGO SETUP --------------------
try:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    quizzes = db["quizzes"]
    users_answers = db["users_answers"]
    logger.info("MongoDB Connected Successfully!")
except Exception as e:
    logger.error(f"Error connecting to MongoDB: {e}")
    exit()

# -------------------- START --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type in ['group', 'supergroup']:
        # <-- CHANGE HERE: Pehle se chal rahe quiz ko check karne ke liye logic
        if context.chat_data.get('quiz_id'):
            await update.message.reply_text("Quiz pehle se hi set up ho raha hai is group mein!")
            return
        
        try:
            payload = context.args[0]
            if payload.startswith("quiz_"):
                quiz_id = payload.split('_')[1]
                quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
                if quiz:
                    chat_id = update.message.chat_id
                    context.chat_data['quiz_id'] = quiz_id
                    context.chat_data['ready_users'] = set()
                    
                    keyboard = [[InlineKeyboardButton("✅ I am ready!", callback_data=f"ready_{quiz_id}")]]
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🎲 Get ready for the quiz '{quiz['title']}'\n\n"
                            f"📝 {len(quiz['questions'])} questions\n"
                            f"👥 The quiz will begin when at least 2 people are ready to play.\n\n"
                            f"Send /stop to cancel."
                        ),
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                return
        except (IndexError, Exception) as e:
            logger.info(f"Bot added to group without payload or error: {e}")
            await update.message.reply_text("Thanks for adding me! Use /start in private chat to create quizzes.")
            return

    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("🆕 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
    ]
    await update.message.reply_text(
        "This bot will help you create and play quizzes with multiple choice questions.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# --- NAYA FUNCTION: Quiz ke liye 5 options dikhane wala ---
async def show_play_options(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_id: str):
    keyboard = [
        [InlineKeyboardButton("▶️ Play in Private", callback_data=f"play_private_{quiz_id}")],
        [InlineKeyboardButton("👥 Play with Friends", callback_data=f"play_group_{quiz_id}")],
        [InlineKeyboardButton("✏️ Edit Quiz", callback_data=f"edit_quiz_{quiz_id}")],
        [InlineKeyboardButton("🗑️ Delete Quiz", callback_data=f"delete_quiz_{quiz_id}")],
        [InlineKeyboardButton("↩️ Go Back", callback_data="start_menu")]
    ]
    if update.callback_query:
        await update.callback_query.message.edit_text(
            "What would you like to do with this quiz?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "What would you like to do with this quiz?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# -------------------- BUTTON HANDLER --------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = context.user_data

    if query.data == "create_quiz":
        user_data.clear()
        user_data["step"] = "title"
        await query.message.reply_text("Send me the *title* of your quiz.", parse_mode='Markdown')
        
    elif query.data == "start_menu":
        keyboard = [
            [InlineKeyboardButton("🆕 Create New Quiz", callback_data="create_quiz")],
            [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
        ]
        await query.message.edit_text(
            "This bot will help you create and play quizzes with multiple choice questions.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "view_quizzes":
        try:
            user_quizzes = list(quizzes.find({"user_id": user_id}))
            if not user_quizzes:
                await query.message.reply_text("❌ You have no saved quizzes.")
                return
            buttons = [
                [InlineKeyboardButton(f"{q['title']}", callback_data=f"play_options_{q['_id']}")]
                for q in user_quizzes
            ]
            await query.message.reply_text("📚 Your quizzes:", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            logger.error(f"Error fetching quizzes from DB: {e}")
            await query.message.reply_text("❌ Could not fetch your quizzes. Please try again later.")

    elif query.data.startswith("play_options_"):
        quiz_id = query.data.replace("play_options_", "")
        await show_play_options(update, context, quiz_id)
        
    elif query.data.startswith("play_") and not query.data.startswith("play_options_") and not query.data.startswith("play_private_") and not query.data.startswith("play_group_") and not query.data.startswith("play_shuffle_") and not query.data.startswith("play_timer_"):
        quiz_id = query.data.replace("play_", "")
        await show_play_options(update, context, quiz_id)

    elif query.data.startswith("play_private_"):
        quiz_id = query.data.replace("play_private_", "")
        keyboard = [
            [InlineKeyboardButton("🔀 Shuffle All", callback_data=f"play_shuffle_{quiz_id}_shuffle_all")],
            [InlineKeyboardButton("❌ No Shuffle", callback_data=f"play_shuffle_{quiz_id}_no_shuffle")],
            [InlineKeyboardButton("🔁 Only Answers", callback_data=f"play_shuffle_{quiz_id}_shuffle_answers")],
            [InlineKeyboardButton("🔂 Only Questions", callback_data=f"play_shuffle_{quiz_id}_shuffle_questions")],
        ]
        await query.message.edit_text(
            "Choose how you want to shuffle this quiz:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    
    elif query.data.startswith("play_group_"):
        quiz_id = query.data.replace("play_group_", "")
        bot_username = (await context.bot.get_me()).username
        group_add_url = f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}"
        keyboard = [[InlineKeyboardButton("Add Bot to a Group", url=group_add_url)]]
        await query.message.edit_text(
            "Click the button below to add me to a group. I will start the quiz there automatically.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- "I am ready!" button ke liye ---
async def ready_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    async with group_locks[chat_id]:
        ready_users = context.chat_data.get('ready_users', set())
        if user_id in ready_users:
            await query.answer("You are already ready!", show_alert=False)
            return
        ready_users.add(user_id)
        context.chat_data['ready_users'] = ready_users
        await query.answer(f"You are ready! ({len(ready_users)}/2)", show_alert=False)
        
        if len(ready_users) >= 2:
            quiz_id = context.chat_data.get('quiz_id')
            await query.message.delete()
            keyboard = [
                [InlineKeyboardButton("10s", callback_data=f"play_timer_{quiz_id}_10"), InlineKeyboardButton("15s", callback_data=f"play_timer_{quiz_id}_15"), InlineKeyboardButton("30s", callback_data=f"play_timer_{quiz_id}_30")],
                [InlineKeyboardButton("45s", callback_data=f"play_timer_{quiz_id}_45"), InlineKeyboardButton("1min", callback_data=f"play_timer_{quiz_id}_60")],
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏱ Select time per question for this quiz:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

# --- Quiz Creation handlers (No changes here) ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data; text = update.message.text
    if "step" not in user_data: return
    state = user_data
    if text == "/skip" and state.get("step") == "description":
        state["description"], state["questions"], state["step"] = "", [], "question"
        await update.message.reply_text("Send your first *question*.", parse_mode='Markdown')
        return
    if state["step"] == "title":
        state["title"], state["step"] = text, "description"
        await update.message.reply_text("Send me a *description*. Or type /skip.", parse_mode='Markdown')
    elif state["step"] == "description":
        state["description"], state["questions"], state["step"] = text, [], "question"
        await update.message.reply_text("Send your first *question*.", parse_mode='Markdown')
    elif state["step"] == "question":
        state["current_question"], state["step"] = {"question": text, "options": []}, "options"
        await update.message.reply_text("Send option 1 for this question:")
    elif state["step"] == "options":
        state["current_question"]["options"].append(text)
        if len(state["current_question"]["options"]) < 2:
            await update.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
        else:
            keyboard = [[InlineKeyboardButton("➕ Add More Option", callback_data="add_option")], [InlineKeyboardButton("✅ Done", callback_data="done_options")]]
            await update.message.reply_text(f"Option {len(state['current_question']['options'])} saved. What next?", reply_markup=InlineKeyboardMarkup(keyboard))

async def options_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); state = context.user_data
    if query.data == "add_option":
        await query.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
    elif query.data == "done_options":
        state["step"] = "correct"; opts = state["current_question"]["options"]
        keyboard = [[InlineKeyboardButton(o, callback_data=f"correct_{i}")] for i, o in enumerate(opts)]
        await query.message.reply_text("Which one is the correct option?", reply_markup=InlineKeyboardMarkup(keyboard))

async def correct_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); state = context.user_data
    if query.data.startswith("correct_"):
        correct_index = int(query.data.replace("correct_", ""))
        state["current_question"]["correct_index"] = correct_index
        if "questions" not in state: state["questions"] = []
        state["questions"].append(state["current_question"]); state.pop("current_question", None)
        state["step"] = "more_questions"
        keyboard = [[InlineKeyboardButton("➕ Add Another Question", callback_data="new_question")], [InlineKeyboardButton("✅ Finish & Save Quiz", callback_data="finish_quiz")]]
        await query.message.reply_text("Question added! What next?", reply_markup=InlineKeyboardMarkup(keyboard))

async def more_questions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); state = context.user_data
    if query.data == "new_question":
        state["step"] = "question"; await query.message.reply_text("Send me the next *question*.", parse_mode='Markdown')
    elif query.data == "finish_quiz":
        try:
            quiz_data = {"user_id": query.from_user.id, "title": state.get("title", "Untitled"), "description": state.get("description", ""), "questions": state.get("questions", [])}
            if not quiz_data["questions"]:
                await query.message.reply_text("❌ Cannot save a quiz with no questions!"); return
            result = quizzes.insert_one(quiz_data)
            await query.message.reply_text(f"✅ Your quiz '{quiz_data['title']}' has been saved!")
            state.clear()
            await show_play_options(update, context, str(result.inserted_id))
        except Exception as e:
            logger.error(f"Error saving new quiz to DB: {e}")
            await query.message.reply_text("❌ Could not save your quiz. Please try again.")

# -------------------- SHUFFLE/PLAY HANDLER --------------------
async def shuffle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    parts = query.data.split("_"); quiz_id = parts[2]; shuffle_option = "_".join(parts[3:])
    if query.message.chat.type == 'private': context.user_data["play_shuffle_option"] = shuffle_option
    else: context.chat_data["play_shuffle_option"] = shuffle_option
    keyboard = [
        [InlineKeyboardButton("10s", callback_data=f"play_timer_{quiz_id}_10"), InlineKeyboardButton("15s", callback_data=f"play_timer_{quiz_id}_15"), InlineKeyboardButton("30s", callback_data=f"play_timer_{quiz_id}_30")],
        [InlineKeyboardButton("45s", callback_data=f"play_timer_{quiz_id}_45"), InlineKeyboardButton("1min", callback_data=f"play_timer_{quiz_id}_60")],
    ]
    await query.message.edit_text("⏱ Select time per question for this quiz:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- FINAL RESULTS HANDLER (LEADERBOARD KE SATH) ---
async def send_final_results(context: ContextTypes.DEFAULT_TYPE, user_id_or_chat_id: int, chat_id: int, quiz_title: str, total_questions: int, start_time: float, is_group: bool):
    await asyncio.sleep(5)
    
    leaderboard_text = f"🏁 The quiz '{quiz_title}' has finished!\n\n"
    
    if is_group:
        data_source = context.application.chat_data.get(chat_id, {})
        player_scores = data_source.get('player_scores', {})
        
        if not player_scores:
            leaderboard_text += "No one participated in the quiz. 🤷"
        else:
            sorted_players = sorted(
                player_scores.values(), 
                key=lambda p: p.get('correct', 0), 
                reverse=True
            )
            
            leaderboard_text += f"🏆 Leaderboard Top {min(len(sorted_players), 10)}:\n\n"
            medals = ["🥇", "🥈", "🥉"]
            
            for i, player in enumerate(sorted_players[:10]):
                name = player.get('name', 'Anonymous')
                correct = player.get('correct', 0)
                
                medal = medals[i] if i < 3 else f"{i + 1}."
                leaderboard_text += f"{medal} {name} - {correct}/{total_questions}\n"

    else:
        # Private quiz ke liye purana logic
        data_source = context.application.user_data.get(user_id_or_chat_id, {})
        correct_count = data_source.get("correct_count", 0)
        wrong_count = data_source.get("wrong_count", 0)
        answered_questions = correct_count + wrong_count
        missed_count = total_questions - answered_questions
        end_time = time.time()
        duration = int(end_time - start_time)
        leaderboard_text += (
            f"You attempted {answered_questions} out of {total_questions} questions:\n\n"
            f"✅ Correct – {correct_count}\n"
            f"❌ Wrong – {wrong_count}\n"
            f"⌛️ Missed – {missed_count}\n"
            f"⏱️ {duration} sec"
        )
        
    await context.bot.send_message(chat_id=chat_id, text=leaderboard_text)
    
    # <-- CHANGE HERE: Bot data se quiz polls ko clean karne ka logic
    if 'poll_to_user' in context.bot_data:
        polls_to_remove = [
            poll_id for poll_id, data in context.bot_data['poll_to_user'].items() 
            if data.get("chat_id") == chat_id
        ]
        for poll_id in polls_to_remove:
            # Using a try-except block just in case another task modifies it
            try:
                del context.bot_data['poll_to_user'][poll_id]
            except KeyError:
                pass
    
    if is_group and chat_id in group_locks:
        del group_locks[chat_id]
    elif not is_group and user_id_or_chat_id in user_locks:
        del user_locks[user_id_or_chat_id]
    
    # Safely clear only quiz-related data
    if chat_id in context.application.chat_data:
        quiz_keys = ['quiz_id', 'ready_users', 'player_scores', 'session_correct_answers', 'play_shuffle_option']
        for key in quiz_keys:
            context.application.chat_data[chat_id].pop(key, None)


# --- PLAY QUIZ HANDLER ---
async def play_timer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    chat_id = query.message.chat_id; start_time = time.time()
    
    is_group = query.message.chat.type in ['group', 'supergroup']
    if is_group:
        data_source, lock, user_id = context.chat_data, group_locks[chat_id], chat_id
    else:
        user_id = query.from_user.id
        data_source, lock = context.user_data, user_locks[user_id]

    parts = query.data.split("_"); quiz_id = parts[2]; timer = int(parts[3])
    
    try:
        quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
        if not quiz: await query.message.reply_text("❌ Quiz not found!"); return
    except Exception as e:
        logger.error(f"Error finding quiz {quiz_id}: {e}"); await query.message.reply_text("❌ Could not start quiz."); return

    shuffle_option = data_source.get("play_shuffle_option", "no_shuffle")
    await query.message.edit_text(f"▶️ Starting quiz: *{quiz['title']}*", parse_mode='Markdown')
    
    questions = copy.deepcopy(quiz["questions"])
    if shuffle_option in ["shuffle_all", "shuffle_questions"]: random.shuffle(questions)

    async with lock:
        if is_group:
            data_source['player_scores'] = {} # Har player ka score yahan store hoga
        else:
            data_source['correct_count'], data_source['wrong_count'] = 0, 0
        data_source["session_correct_answers"] = {}

    if 'poll_to_user' not in context.bot_data: context.bot_data['poll_to_user'] = {}

    for idx, q in enumerate(questions, start=1):
        options = q["options"][:]; correct_index = q["correct_index"]
        if shuffle_option in ["shuffle_all", "shuffle_answers"]:
            paired = list(enumerate(options)); random.shuffle(paired)
            new_indices, new_options = zip(*paired)
            options, shuffled_correct_index = list(new_options), list(new_indices).index(correct_index)
        else:
            shuffled_correct_index = correct_index

        async with lock: data_source["session_correct_answers"][idx] = shuffled_correct_index

        poll_message = await context.bot.send_poll(
            chat_id=chat_id, question=f"Q{idx}: {q['question']}", options=options,
            type=Poll.QUIZ, correct_option_id=int(shuffled_correct_index),
            open_period=timer, is_anonymous=False,
        )
        context.bot_data['poll_to_user'][poll_message.poll.id] = {"chat_id": chat_id, "is_group": is_group, "question_idx": idx}
        await asyncio.sleep(timer)
    
    asyncio.create_task(
        send_final_results(
            context=context, user_id_or_chat_id=user_id, chat_id=chat_id,
            quiz_title=quiz['title'], total_questions=len(questions),
            start_time=start_time, is_group=is_group
        )
    )

# -------------------- POLL ANSWER HANDLER (UPDATED) --------------------
async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_id = update.poll_answer.poll_id
    poll_map = context.bot_data.get('poll_to_user', {})
    if poll_id not in poll_map: return
    
    # <-- CHANGE HERE: .pop() ko .get() se badal diya gaya hai
    poll_info = poll_map.get(poll_id)
    if not poll_info: return

    chat_id, is_group, question_idx = poll_info["chat_id"], poll_info["is_group"], poll_info["question_idx"]
    user = update.poll_answer.user
    
    lock = group_locks[chat_id] if is_group else user_locks[user.id]
    
    async with lock:
        data_source = context.application.chat_data.get(chat_id) if is_group else context.application.user_data.get(user.id)
        if not data_source or not update.poll_answer.option_ids: return

        selected_option = update.poll_answer.option_ids[0]
        correct_option = data_source.get("session_correct_answers", {}).get(question_idx)
        is_correct = (selected_option == correct_option)

        if is_group:
            if 'player_scores' not in data_source: data_source['player_scores'] = {}
            player_id_str = str(user.id)
            if player_id_str not in data_source['player_scores']:
                data_source['player_scores'][player_id_str] = {'name': user.first_name, 'correct': 0, 'wrong': 0}
            
            if is_correct:
                data_source['player_scores'][player_id_str]['correct'] += 1
            else:
                data_source['player_scores'][player_id_str]['wrong'] += 1
        else:
            if is_correct: data_source['correct_count'] = data_source.get('correct_count', 0) + 1
            else: data_source['wrong_count'] = data_source.get('wrong_count', 0) + 1


# -------------------- MAIN --------------------
def main():
    persistence = PicklePersistence(filepath="bot_state_data")
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^(create_quiz|view_quizzes|play_options_|play_private_|play_group_|start_menu|play_(?!timer_|shuffle_).*)$"))
    application.add_handler(CallbackQueryHandler(options_button, pattern="^(add_option|done_options)$"))
    application.add_handler(CallbackQueryHandler(correct_button, pattern="^correct_.*$"))
    application.add_handler(CallbackQueryHandler(more_questions_handler, pattern="^(new_question|finish_quiz)$"))
    application.add_handler(CallbackQueryHandler(shuffle_handler, pattern="^play_shuffle_.*$"))
    application.add_handler(CallbackQueryHandler(play_timer_handler, pattern="^play_timer_.*$"))
    application.add_handler(CallbackQueryHandler(ready_button_handler, pattern="^ready_.*$"))
    application.add_handler(PollAnswerHandler(poll_answer_handler))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
