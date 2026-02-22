import sqlite3
import math
from datetime import datetime
from io import StringIO, BytesIO
import csv
import asyncio

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ================== CONFIG ==================

# !!! هام: استبدل هذا بالتوكن الحقيقي للبوت الخاص بك !!!
BOT_TOKEN = "8533640296:AAEx4PUx7RzfeVB3EkTBz4iCe09xG2EjUuY" # <--- ضع التوكن هنا

# رقم PIN للدوكتور (يمكنك تغييره)
DOCTOR_PIN = "1234"

# نصف القطر المسموح به لتسجيل الحضور (بالمتر)
ALLOWED_RADIUS = 100

DB_NAME = "attendance_v4.db" # استخدام قاعدة بيانات جديدة لتجنب أي تعارض

# ===========================================

# ================== DATABASE ==================

def init_db():
    """تهيئة وإنشاء جداول قاعدة البيانات إذا لم تكن موجودة."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            student_code TEXT NOT NULL,
            registration_date TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            latitude REAL,
            longitude REAL,
            timestamp TEXT,
            date_only TEXT,
            FOREIGN KEY (telegram_id) REFERENCES students (telegram_id),
            UNIQUE(telegram_id, date_only)
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        conn.commit()

# ================== HELPERS ==================

def get_setting(key, default=None):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        result = c.fetchone()
        return result[0] if result else default

def set_setting(key, value):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_today_date():
    return datetime.now().strftime("%Y-%m-%d")

def get_student_data(telegram_id):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT name, student_code FROM students WHERE telegram_id = ?", (telegram_id,))
        return c.fetchone()

def save_student_data(telegram_id, name, student_code):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO students (telegram_id, name, student_code, registration_date) VALUES (?, ?, ?, ?)",
            (telegram_id, name, student_code, datetime.now().isoformat())
        )
        conn.commit()

# ================== STUDENT HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    student_data = get_student_data(user.id)
    
    if student_data:
        name, code = student_data
        keyboard = [
            [InlineKeyboardButton("✅ تسجيل الحضور الآن", callback_data="attend_now")],
           
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🎓 أهلاً بعودتك **{name}**!\n\n"
            f"بياناتك المسجلة:\n"
            f"👤 الاسم: `{name}`\n"
            f"🔢 الكود: `{code}`\n\n"
            "اضغط على الزر أدناه لتسجيل حضورك.",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        keyboard = [ 
            [InlineKeyboardButton("👨‍🎓 تسجيل كطالب جديد", callback_data="register_student")],
            [InlineKeyboardButton("👨‍🏫 أنا دكتور", callback_data="login_doctor")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🎓 مرحباً بك في نظام تسجيل الحضور الإلكتروني.\n\n"
            "من فضلك، اختر دورك للبدء:",
            reply_markup=reply_markup
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    context.user_data["next_step"] = None

    if action == "register_student" or action == "edit_my_data":
        if action == "edit_my_data":
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("DELETE FROM students WHERE telegram_id = ?", (query.from_user.id,))
            await query.edit_message_text(text="تم حذف بياناتك القديمة. لنبدأ من جديد.")
        
        await query.edit_message_text(text="✍️ لتسجيل بياناتك، يرجى كتابة **اسمك الكامل**:", parse_mode=ParseMode.MARKDOWN)
        context.user_data["next_step"] = "get_name"

    elif action == "login_doctor":
        await query.edit_message_text(text="🔐 للدخول إلى لوحة التحكم، يرجى إدخال رقم PIN الخاص بالدكتور:")
        context.user_data["next_step"] = "doctor_pin"

    elif action == "attend_now":
        with sqlite3.connect(DB_NAME) as conn:
            already_attended = conn.execute("SELECT id FROM attendance WHERE telegram_id = ? AND date_only = ?", (query.from_user.id, get_today_date())).fetchone()
        
        if already_attended:
            await query.answer("✅ لقد قمت بتسجيل حضورك بالفعل اليوم!", show_alert=True)
            return

        btn = KeyboardButton(text="📍 إرسال موقعي الحالي للتسجيل", request_location=True)
        keyboard = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        
        msg = await query.edit_message_text(text="لتسجيل حضورك، يرجى مشاركة موقعك الحالي بالضغط على الزر أدناه.")
        context.user_data["msg_to_delete"] = msg.message_id
        context.user_data["next_step"] = "attendance_location"

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    next_step = context.user_data.get("next_step")
    
    # حذف الرسائل غير المرغوب فيها
    if not next_step:
        try:
            await update.message.delete()
        except (BadRequest, AttributeError):
            pass
        return

    user_input = update.message.text.strip()

    if next_step == "doctor_pin":
        await update.message.delete()
        if user_input == DOCTOR_PIN:
            context.user_data["is_doctor"] = True
            context.user_data.pop("next_step")
            await show_doctor_panel(update, context)
        else:
            msg = await update.message.reply_text("❌ رقم PIN غير صحيح. حاول مرة أخرى.")
            await asyncio.sleep(3)
            await msg.delete()
        return

    if next_step == "get_name":
        context.user_data["name"] = user_input
        context.user_data["next_step"] = "get_code"
        await update.message.reply_text("👍 تمام. الآن أدخل **الكود الجامعي** (ID):", parse_mode=ParseMode.MARKDOWN)
    
    elif next_step == "get_code":
        context.user_data["student_code"] = user_input
        try:
            save_student_data(update.effective_user.id, context.user_data["name"], context.user_data["student_code"])
            await update.message.reply_text(
                f"✅ تم حفظ بياناتك بنجاح!\n\n"
                f"👤 الاسم: {context.user_data['name']}\n"
                f"🔢 الكود: {context.user_data['student_code']}\n\n"
                "اضغط /start للبدء."
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ خطأ: هذا الحساب مسجل بالفعل. استخدم /start للبدء.")
        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")
        context.user_data.clear()

# ================== LOCATION HANDLER (MODIFIED) ==================

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالجة إرسال الموقع بشكل آمن واحترافي.
    """
    # --[ بداية التعديل ]--
    # التحقق من أن التحديث يحتوي على رسالة وموقع قبل المتابعة
    if not update.message or not update.message.location:
        # هذا التحديث ليس رسالة موقع، تجاهله بأمان
        return
    
    loc = update.message.location
    # --[ نهاية التعديل ]--

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # حذف لوحة مفاتيح طلب الموقع
    await update.message.reply_text("جاري معالجة الموقع...", reply_markup=ReplyKeyboardRemove())

    if "msg_to_delete" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["msg_to_delete"])
        except (BadRequest, AttributeError):
            pass

    if context.user_data.get("is_doctor") and context.user_data.get("next_step") == "doctor_location":
        set_setting('lecture_lat', loc.latitude)
        set_setting('lecture_lon', loc.longitude)
        context.user_data.pop("next_step", None)
        await update.message.reply_text("✅ تم تحديث موقع المحاضرة بنجاح.")
        await show_doctor_panel(update, context)
        return

    if context.user_data.get("next_step") == "attendance_location":
        context.user_data.pop("next_step", None)
        
        if get_setting("registration_active", "false") != "true":
            await update.message.reply_text("⛔ عذراً، التسجيل مغلق حالياً من قبل الدكتور.")
            return
        
        doctor_lat = get_setting('lecture_lat')
        doctor_lon = get_setting('lecture_lon')

        if not doctor_lat or not doctor_lon:
            await update.message.reply_text("⛔ عذراً، لم يحدد الدكتور موقع المحاضرة بعد.")
            return

        distance = get_distance(loc.latitude, loc.longitude, float(doctor_lat), float(doctor_lon))
        
        if distance > ALLOWED_RADIUS:
            await update.message.reply_text(
                f"❌ **أنت خارج النطاق المسموح به!**\n\n"
                f"📏 المسافة الحالية: **{int(distance)} متر**\n"
                f"📍 المسافة المسموحة: **{ALLOWED_RADIUS} متر**\n\n"
                "حاول الاقتراب والمحاولة مرة أخرى عبر /start.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("""
                INSERT INTO attendance (telegram_id, latitude, longitude, timestamp, date_only)
                VALUES (?, ?, ?, ?, ?)
                """, (user_id, loc.latitude, loc.longitude, datetime.now().isoformat(), get_today_date()))
            
            student_name = get_student_data(user_id)[0]
            await update.message.reply_text(
                f"✅ **تم تسجيل حضورك بنجاح، {student_name}!**\n"
                f"🕒 الوقت: {datetime.now().strftime('%H:%M:%S')}",
                parse_mode=ParseMode.MARKDOWN
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text("✅ لقد قمت بتسجيل حضورك بالفعل اليوم!")
        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")
        
        context.user_data.clear()

# ================== DOCTOR PANEL & EXPORT ==================

async def show_doctor_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_active = get_setting("registration_active", "false") == "true"
    status_text = "🟢 مفتوح" if is_active else "🔴 مغلق"
    keyboard = [
        [InlineKeyboardButton("📍 تحديث موقع المحاضرة", callback_data="doc_update_loc")],
        [InlineKeyboardButton("🔴 إغلاق التسجيل", callback_data="doc_deactivate") if is_active else InlineKeyboardButton("🟢 فتح التسجيل", callback_data="doc_activate")],
        [InlineKeyboardButton("📋 عدد الحاضرين اليوم", callback_data="doc_count_today")],
        [InlineKeyboardButton("📊 تصدير حضور اليوم", callback_data="doc_export_today")],
        [InlineKeyboardButton("🗓️ تصدير حضور الشهر", callback_data="doc_export_month")],
        [InlineKeyboardButton("❌ خروج من اللوحة", callback_data="doc_logout")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = f"👨‍🏫 **لوحة تحكم الدكتور**\n\nحالة التسجيل: **{status_text}**"
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except BadRequest: pass
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def doctor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "doc_update_loc":
        context.user_data["next_step"] = "doctor_location"
        btn = KeyboardButton(text="📍 إرسال موقع المحاضرة الحالي", request_location=True)
        keyboard = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await query.edit_message_text(text="لتحديث موقع المحاضرة، يرجى الضغط على الزر أدناه وإرسال الموقع.")
        await query.message.reply_text("👇", reply_markup=keyboard)
    elif action == "doc_deactivate":
        set_setting("registration_active", "false")
        await query.answer("🔴 تم إيقاف التسجيل.", show_alert=True)
    elif action == "doc_activate":
        set_setting("registration_active", "true")
        await query.answer("🟢 تم تفعيل التسجيل.", show_alert=True)
    elif action == "doc_count_today":
        with sqlite3.connect(DB_NAME) as conn:
            count = conn.execute("SELECT COUNT(*) FROM attendance WHERE date_only = ?", (get_today_date(),)).fetchone()[0]
        await query.answer(f"عدد الطلاب الحاضرين اليوم: {count}", show_alert=True)
    elif action == "doc_export_today":
        await export_attendance(query, "today")
    elif action == "doc_export_month":
        await export_attendance(query, "month")
    elif action == "doc_logout":
        context.user_data.clear()
        await query.edit_message_text(text="👋 تم الخروج من لوحة التحكم بنجاح.")
    
    if action not in ["doc_logout", "doc_update_loc"]:
        await show_doctor_panel(update, context)

async def export_attendance(query, export_type):
    with sqlite3.connect(DB_NAME) as conn:
        if export_type == "today":
            sql = "SELECT s.name, s.student_code, a.timestamp FROM attendance a JOIN students s ON a.telegram_id = s.telegram_id WHERE a.date_only = ? ORDER BY a.timestamp"
            params = (get_today_date(),)
            filename = f"attendance_{get_today_date()}.csv"
            title = f"بيانات الحضور ليوم {get_today_date()}"
            headers = ["الاسم", "الكود الجامعي", "وقت التسجيل"]
        else:
            month_filter = datetime.now().strftime("%Y-%m")
            sql = "SELECT s.name, s.student_code, a.timestamp, a.date_only FROM attendance a JOIN students s ON a.telegram_id = s.telegram_id WHERE strftime('%Y-%m', a.date_only) = ? ORDER BY a.timestamp"
            params = (month_filter,)
            filename = f"attendance_{month_filter}.csv"
            title = f"بيانات الحضور لشهر {month_filter}"
            headers = ["الاسم", "الكود الجامعي", "وقت التسجيل", "تاريخ التسجيل"]
        
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        await query.answer("📭 لا توجد بيانات حضور للتصدير.", show_alert=True)
        return
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    csv_content = output.getvalue().encode('utf-8-sig')
    output.close()
    
    await query.message.reply_document(document=BytesIO(csv_content), filename=filename, caption=f"📊 {title}\n- عدد السجلات: {len(rows)}")

# ================== MAIN ==================

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^(register_student|edit_my_data|login_doctor|attend_now)$"))
    app.add_handler(CallbackQueryHandler(doctor_callback, pattern="^doc_"))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()


