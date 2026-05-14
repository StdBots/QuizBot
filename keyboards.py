from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📋 My Quizzes", callback_data="my_quizzes")],
    ])


def quiz_card_kb(quiz_id: str, bot_username: str):
    """Shown after quiz is created / when viewing a quiz."""
    share_link = f"https://t.me/{bot_username}?start={quiz_id}"
    group_link = f"https://t.me/{bot_username}?startgroup={quiz_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start this quiz", callback_data=f"start_dm_{quiz_id}")],
        [InlineKeyboardButton("👥 Start quiz in group", url=group_link)],
        [InlineKeyboardButton("🔗 Share quiz", url=share_link)],
        [InlineKeyboardButton("✏️ Edit quiz", callback_data=f"edit_{quiz_id}")],
        [InlineKeyboardButton("📊 Quiz stats", callback_data=f"stats_{quiz_id}")],
    ])


def timer_kb(quiz_id: str, context: str = "dm"):
    """Timer selection — 10s to 5min like original."""
    prefix = f"timer_{context}_{quiz_id}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("10 sec", callback_data=f"{prefix}_10"),
            InlineKeyboardButton("15 sec", callback_data=f"{prefix}_15"),
            InlineKeyboardButton("30 sec", callback_data=f"{prefix}_30"),
        ],
        [
            InlineKeyboardButton("45 sec", callback_data=f"{prefix}_45"),
            InlineKeyboardButton("1 min",  callback_data=f"{prefix}_60"),
            InlineKeyboardButton("2 min",  callback_data=f"{prefix}_120"),
        ],
        [
            InlineKeyboardButton("3 min",  callback_data=f"{prefix}_180"),
            InlineKeyboardButton("4 min",  callback_data=f"{prefix}_240"),
            InlineKeyboardButton("5 min",  callback_data=f"{prefix}_300"),
        ],
    ])


def shuffle_kb(quiz_id: str, context: str = "dm"):
    prefix = f"shuffle_{context}_{quiz_id}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔀 Shuffle All",     callback_data=f"{prefix}_all"),
            InlineKeyboardButton("❌ No Shuffle",       callback_data=f"{prefix}_none"),
        ],
        [
            InlineKeyboardButton("❓ Only Questions",  callback_data=f"{prefix}_questions"),
            InlineKeyboardButton("🅰️ Only Answers",    callback_data=f"{prefix}_answers"),
        ],
    ])


def ready_kb(quiz_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☑️ I am ready!", callback_data=f"ready_{quiz_id}")]
    ])


def edit_quiz_kb(quiz_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add question",        callback_data=f"edit_add_{quiz_id}")],
        [InlineKeyboardButton("🗑 Remove a question",   callback_data=f"edit_remove_{quiz_id}")],
        [InlineKeyboardButton("📝 Rename quiz",         callback_data=f"edit_rename_{quiz_id}")],
        [InlineKeyboardButton("↩️ Back",                callback_data=f"quiz_card_{quiz_id}")],
    ])


def remove_questions_kb(quiz_id: str, questions: list):
    rows = [
        [InlineKeyboardButton(f"🗑 {i+1}. {q['question'][:45]}", callback_data=f"rmq_{quiz_id}_{i}")]
        for i, q in enumerate(questions)
    ]
    rows.append([InlineKeyboardButton("↩️ Back", callback_data=f"edit_{quiz_id}")])
    return InlineKeyboardMarkup(rows)


def my_quizzes_kb(quizzes: list):
    rows = [
        [InlineKeyboardButton(f"📋 {q['title']} ({len(q.get('questions',[]))} Qs)", callback_data=f"quiz_card_{q['_id']}")]
        for q in quizzes
    ]
    rows.append([InlineKeyboardButton("↩️ Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def share_quiz_kb(bot_username: str, quiz_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Share quiz", url=f"https://t.me/{bot_username}?start={quiz_id}")]
    ])


def create_question_poll_kb():
    """Native Telegram poll button in reply keyboard."""
    btn = KeyboardButton("📊 Create a question", request_poll={"type": "quiz"})
    return ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
