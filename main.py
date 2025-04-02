import logging
import os
import sys
import smtplib
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

# Импорт функции для работы с БД
from database import init_db, add_admin, is_admin, save_ticket, update_status, get_user_id_by_ticket, get_tickets_by_status, save_feedback, get_feedback

# Загружаем переменные из .env
load_dotenv()

# Токен бота
TOKEN = os.getenv("BOT_TOKEN")

# Настройки email для Яндекс.Почты
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

# Максимальное количество вложений
MAX_ATTACHMENTS = 3

# Файл блокировки для проверки одного экземпляра
LOCK_FILE = "bot.lock"

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для заявки
STATES = {
    'START': 0,
    'CONFIG': 1,
    'ORG_DEPT': 2,
    'NAME': 3,
    'PHONE': 4,
    'DESCRIPTION': 5,
    'FEEDBACK': 6
}

# Асинхронная функция для загрузки файла из Telegram
async def download_file(bot, file_id, file_name):
    file = await bot.get_file(file_id)
    file_path = f"temp_{file_id}_{file_name}"  # Уникальное имя файла
    await file.download_to_drive(file_path)
    return file_path

# Отправка email-уведомления через Яндекс Почту
def send_email(ticket_id, config, org_dept, name, phone, description, attachments=None):
    subject = f"Новая заявка #{ticket_id} в техподдержку"
    body = (
        f"Новая заявка #{ticket_id}:\n"
        f"Конфигурация: {config}\n"
        f"Организация и отдел: {org_dept}\n"
        f"Имя: {name}\n"
        f"Номер телефона: {phone}\n"
        f"Описание: {description}\n"
        f"Статус: Принято"
    )

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = EMAIL_USER  # Адрес отправителя совпадает с логином SMTP
    msg['To'] = ADMIN_EMAIL
    msg.attach(MIMEText(body, 'plain'))

    if attachments:
        for file_path in attachments:
            try:
                with open(file_path, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(file_path)}')
                    msg.attach(part)
            except Exception as e:
                logger.error(f"Ошибка при прикреплении файла {file_path}: {e}")

    try:
        # Используем SMTP_SSL для порта 465
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, context=ssl.create_default_context()) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            logger.info(f"Email с заявкой #{ticket_id} отправлен на {ADMIN_EMAIL} через {EMAIL_HOST} (порт {EMAIL_PORT})")
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"Ошибка аутентификации: Проверьте логин ({EMAIL_USER}) и пароль. Ошибка: {e}")
    except smtplib.SMTPConnectError as e:
        logger.error(f"Ошибка соединения с {EMAIL_HOST}:{EMAIL_PORT}. Проверьте порт и доступность сервера. Ошибка: {e}")
    except Exception as e:
        logger.error(f"Общая ошибка отправки email: {e}")
    finally:
        if attachments:
            for file_path in attachments:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"Удалён временный файл: {file_path}")
                except Exception as e:
                    logger.error(f"Ошибка при удалении файла {file_path}: {e}")

# Функция для удаления сообщения через заданное время
async def delete_message(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.context['chat_id']
    message_id = context.job.context['message_id']
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Сообщение {message_id} удалено из чата {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения {message_id} из чата {chat_id}: {e}")

# Приветственное сообщение
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Оставить заявку 📝", callback_data='create_ticket')],
        [InlineKeyboardButton("Справка 📚", callback_data='help')]
    ]
    if is_admin(update.effective_user.id):
        keyboard.append([InlineKeyboardButton("Панель администратора ⚙️", callback_data='admin_panel')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            "Добро пожаловать в бот техподдержки! 👋\n"
            "Я помогу вам оставить заявку. Выберите действие: ⬇️",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.message.reply_text(
            "Добро пожаловать в бот техподдержки! 👋\n"
            "Я помогу вам оставить заявку. Выберите действие: ⬇️",
            reply_markup=reply_markup
        )
    context.user_data['state'] = STATES['START']
    logger.info(f"Приветственное сообщение отправлено пользователю {update.effective_user.id}")

# Функция для обработки кнопки "Справка"
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    help_text = (
        "📚 **Как правильно заполнять данные для заявки**\n\n"
        "Чтобы мы могли оперативно обработать вашу заявку, пожалуйста, следуйте этим рекомендациям:\n\n"
        "1. **Выберите конфигурацию**:\n"
        "   Укажите, с какой программой связана ваша проблема (например, 'Бухгалтерия предприятия', 'ЗУП' и т.д.).\n\n"
        "2. **Укажите организацию и отдел**:\n"
        "   Напишите название вашей организации и отдела (например, 'ООО Ромашка, IT-отдел'). Это поможет нам быстрее найти нужного специалиста.\n\n"
        "3. **Введите ваше ФИО**:\n"
        "   Укажите ваше полное имя, чтобы мы знали, с кем связаться.\n\n"
        "4. **Укажите номер телефона**:\n"
        "   Введите номер в любом формате (например, +79991234567, 8-999-123-45-67 или другой) или поделитесь им через кнопку. Это нужно для связи с вами.\n\n"
        "5. **Опишите проблему**:\n"
        "   Подробно расскажите, что случилось. Если есть скриншоты, видео или документы, приложите их (до 3 вложений).\n\n"
        "🎥 **Видео-инструкция**:\n"
        "Посмотрите видео, чтобы узнать, как правильно заполнить заявку: [Видео-инструкция](https://example.com/video.mp4)\n\n"
        "Теперь, когда вы знаете, как правильно заполнить заявку, давайте приступим! 🚀"
    )
    keyboard = [
        [InlineKeyboardButton("Создать заявку 📝", callback_data='create_ticket')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

# Обработка кнопок
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'create_ticket':
        keyboard = [
            [InlineKeyboardButton("Бухгалтерия предприятия", callback_data='config_bp')],
            [InlineKeyboardButton("ЗУП", callback_data='config_zup')],
            [InlineKeyboardButton("УНФ", callback_data='config_unf')],
            [InlineKeyboardButton("УТ", callback_data='config_ut')],
            [InlineKeyboardButton("Документооборот", callback_data='config_doc')],
            [InlineKeyboardButton("Общепит", callback_data='config_food')],
            [InlineKeyboardButton("Другая", callback_data='config_other')],
            [InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Выберите конфигурацию: ⬇️",
            reply_markup=reply_markup
        )
        context.user_data['state'] = STATES['CONFIG']

    elif query.data == 'help':
        await help(update, context)

    elif query.data.startswith('config_'):
        config_map = {
            'config_bp': 'Бухгалтерия предприятия',
            'config_zup': 'ЗУП',
            'config_unf': 'УНФ',
            'config_ut': 'УТ',
            'config_doc': 'Документооборот',
            'config_food': 'Общепит',
            'config_other': 'Другая'
        }
        config = config_map[query.data]
        context.user_data['config'] = config
        keyboard = [[InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🛑 Укажите наименование организации и отдел (например, ООО Ромашка, IT-отдел): 🏢",
            reply_markup=reply_markup
        )
        context.user_data['state'] = STATES['ORG_DEPT']

    elif query.data == 'back_to_start':
        context.user_data.clear()
        await start(update, context)

    elif query.data == 'admin_panel' and is_admin(query.from_user.id):
        await admin_panel(update, context)

    elif query.data.startswith('status_'):
        ticket_id, new_status = query.data.split('_')[1], query.data.split('_')[2]
        update_status(ticket_id, new_status)
        await notify_user(ticket_id, new_status, context)
        await admin_panel(update, context)

    elif query.data.startswith('rate_'):
        parts = query.data.split('_')
        if len(parts) != 3:
            logger.error(f"Некорректный callback_data: {query.data}")
            await query.edit_message_text("Произошла ошибка при обработке отзыва. Попробуйте снова.")
            return
        ticket_id, rating_str = parts[1], parts[2]
        try:
            rating = int(rating_str)
            if rating not in range(1, 6):
                raise ValueError("Рейтинг вне диапазона")
        except ValueError:
            logger.error(f"Некорректный рейтинг: {rating_str} для ticket_id: {ticket_id}")
            await query.edit_message_text("Произошла ошибка при обработке отзыва. Попробуйте снова.")
            return

        rating_text = {5: "Отлично", 4: "Хорошо", 3: "Нормально", 2: "Плохо", 1: "Ужасно"}[rating]
        logger.info(f"Сохранение отзыва для ticket_id: {ticket_id}, рейтинг: {rating} ({rating_text})")
        save_feedback(ticket_id, rating)
        await query.edit_message_text(f"Спасибо за ваш отзыв: {rating_text} ({rating}/5)! 🙌")

        # Планируем удаление сообщения с подтверждением оценки через 30 секунд
        context.job_queue.run_once(
            delete_message,
            30,
            context={
                'chat_id': update.effective_chat.id,
                'message_id': query.message.message_id
            }
        )

        context.user_data.clear()
        await start(update, context)  # Возвращаемся в начало после оценки

    elif query.data == 'finish_ticket':
        await save_and_finish(update, context)

# Обработка ввода текста
async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get('state', 0)
    text = update.message.text

    if state == STATES['ORG_DEPT']:
        context.user_data['org_dept'] = text
        keyboard = [[InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Укажите ваше ФИО: 👤",
            reply_markup=reply_markup
        )
        context.user_data['state'] = STATES['NAME']

    elif state == STATES['NAME']:
        context.user_data['name'] = text
        keyboard = [[InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        phone_keyboard = [[KeyboardButton("Поделиться номером 📱", request_contact=True)]]
        phone_reply_markup = ReplyKeyboardMarkup(phone_keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "🛑 Укажите ваш номер телефона (в любом формате, например, +79991234567, 8-999-123-45-67) или поделитесь им: 📞",
            reply_markup=reply_markup
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Выберите способ ввода номера:",
            reply_markup=phone_reply_markup
        )
        context.user_data['state'] = STATES['PHONE']

    elif state == STATES['PHONE']:
        # Убрали проверку на формат номера, принимаем любой текст
        context.user_data['phone'] = text
        keyboard = [[InlineKeyboardButton("Завершить заявку ✅", callback_data='finish_ticket')],
                    [InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = await update.message.reply_text(
            f"Опишите проблему текстом или отправьте до {MAX_ATTACHMENTS} вложений (фото, видео, документы).\n"
            f"Уже добавлено: 0 из {MAX_ATTACHMENTS}. Когда закончите, нажмите 'Завершить заявку'. ✍️",
            reply_markup=reply_markup
        )
        context.user_data['state'] = STATES['DESCRIPTION']
        context.user_data['attachments'] = []
        context.user_data['description'] = ""
        context.user_data['last_message_id'] = message.message_id  # Сохраняем ID сообщения

    elif state == STATES['DESCRIPTION']:
        if text.lower() == 'нет':
            if not context.user_data['attachments']:
                context.user_data['description'] = "Без описания"
            await save_and_finish(update, context)
        else:
            context.user_data['description'] += f"{text}\n"
            keyboard = [[InlineKeyboardButton("Завершить заявку ✅", callback_data='finish_ticket')],
                        [InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            # Удаляем предыдущее сообщение
            if 'last_message_id' in context.user_data:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=context.user_data['last_message_id']
                    )
                except Exception as e:
                    logger.error(f"Ошибка при удалении сообщения: {e}")
            # Отправляем новое сообщение
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Текст добавлен. Можете отправить до {MAX_ATTACHMENTS - len(context.user_data['attachments'])} вложений или завершить заявку.",
                reply_markup=reply_markup
            )
            context.user_data['last_message_id'] = message.message_id  # Сохраняем ID нового сообщения

# Обработка номера телефона через кнопку
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get('state', 0)
    if state == STATES['PHONE'] and update.message.contact:
        phone_number = update.message.contact.phone_number
        context.user_data['phone'] = phone_number
        keyboard = [[InlineKeyboardButton("Завершить заявку ✅", callback_data='finish_ticket')],
                    [InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = await update.message.reply_text(
            f"Опишите проблему текстом или отправьте до {MAX_ATTACHMENTS} вложений (фото, видео, документы).\n"
            f"Уже добавлено: 0 из {MAX_ATTACHMENTS}. Когда закончите, нажмите 'Завершить заявку'. ✍️",
            reply_markup=reply_markup
        )
        context.user_data['state'] = STATES['DESCRIPTION']
        context.user_data['attachments'] = []
        context.user_data['description'] = ""
        context.user_data['last_message_id'] = message.message_id  # Сохраняем ID сообщения

# Обработка всех типов вложений
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get('state', 0)
    if state != STATES['DESCRIPTION']:
        return

    attachments = context.user_data.get('attachments', [])
    remaining = MAX_ATTACHMENTS - len(attachments)
    if remaining <= 0:
        keyboard = [[InlineKeyboardButton("Завершить заявку ✅", callback_data='finish_ticket')],
                    [InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Удаляем предыдущее сообщение
        if 'last_message_id' in context.user_data:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['last_message_id']
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения: {e}")
        # Отправляем новое сообщение
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Вы уже отправили максимум {MAX_ATTACHMENTS} вложений. Нажмите 'Завершить заявку' или начните заново.",
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id
        return

    new_attachments = []
    description_updates = []

    # Обрабатываем только одно вложение за раз
    if update.message.photo:
        # Берём только последнее фото (самое большое)
        photo = update.message.photo[-1]
        file_id = photo.file_id
        file_name = f"photo_{file_id}.jpg"
        file_path = await download_file(context.bot, file_id, file_name)
        new_attachments.append(file_path)
        description_updates.append("Прикреплённое фото")
    elif update.message.video:
        file_id = update.message.video.file_id
        file_name = f"video_{file_id}.mp4"
        file_path = await download_file(context.bot, file_id, file_name)
        new_attachments.append(file_path)
        description_updates.append("Прикреплённое видео")
    elif update.message.document:
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name
        file_path = await download_file(context.bot, file_id, file_name)
        new_attachments.append(file_path)
        description_updates.append(f"Прикреплённый документ: {file_name}")
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_name = update.message.audio.file_name
        file_path = await download_file(context.bot, file_id, file_name)
        new_attachments.append(file_path)
        description_updates.append(f"Прикреплённое аудио: {file_name}")
    elif update.message.voice:
        file_id = update.message.voice.file_id
        file_name = f"voice_{file_id}.ogg"
        file_path = await download_file(context.bot, file_id, file_name)
        new_attachments.append(file_path)
        description_updates.append("Прикреплённое голосовое сообщение")
    elif update.message.sticker:
        file_id = update.message.sticker.file_id
        file_name = f"sticker_{file_id}.webp"
        file_path = await download_file(context.bot, file_id, file_name)
        new_attachments.append(file_path)
        description_updates.append("Прикреплённый стикер")

    if new_attachments:
        attachments.extend(new_attachments)
        context.user_data['attachments'] = attachments
        context.user_data['description'] += "\n".join(description_updates) + "\n"

        remaining = MAX_ATTACHMENTS - len(attachments)
        keyboard = [[InlineKeyboardButton("Завершить заявку ✅", callback_data='finish_ticket')],
                    [InlineKeyboardButton("Назад ⬅️", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Удаляем предыдущее сообщение
        if 'last_message_id' in context.user_data:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['last_message_id']
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения: {e}")
        # Отправляем новое сообщение
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🛑 Опишите проблему текстом или отправьте до {remaining} вложений (фото, видео, документы).\n"
                 f"Уже добавлено: {len(attachments)} из {MAX_ATTACHMENTS}. Когда закончите, нажмите 'Завершить заявку'. ✍️",
            reply_markup=reply_markup
        )
        context.user_data['last_message_id'] = message.message_id

# Сохранение заявки
async def save_and_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = context.user_data.get('config', 'Не указано')
    org_dept = context.user_data.get('org_dept', 'Не указано')
    name = context.user_data.get('name', 'Не указано')
    phone = context.user_data.get('phone', 'Не указано')
    description = context.user_data.get('description', 'Без описания').strip()
    attachments = context.user_data.get('attachments', None)

    ticket_id = save_ticket(user_id, config, org_dept, name, phone, description)
    if ticket_id:
        send_email(ticket_id, config, org_dept, name, phone, description, attachments)

        response_text = (
            f"Заявка #{ticket_id} принята! ✅\n"
            f"Конфигурация: {config} 💻\n"
            f"Организация и отдел: {org_dept} 🏢\n"
            f"Имя: {name} 👤\n"
            f"Номер телефона: {phone} 📞\n"
            f"Описание: {description} ✍️\n"
            "Заявка будет обработана в ближайшее время! ⏳"
        )

        # Удаляем предыдущее сообщение, если оно есть
        if 'last_message_id' in context.user_data:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['last_message_id']
                )
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения: {e}")

        # Отправляем итоговое сообщение
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=response_text
        )

        # Очищаем данные
        context.user_data.clear()

        # Отправляем приветственное сообщение напрямую
        keyboard = [
            [InlineKeyboardButton("Оставить заявку 📝", callback_data='create_ticket')],
            [InlineKeyboardButton("Справка 📚", callback_data='help')]
        ]
        if is_admin(update.effective_user.id):
            keyboard.append([InlineKeyboardButton("Панель администратора ⚙️", callback_data='admin_panel')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Добро пожаловать в бот техподдержки! 👋\nЯ помогу вам оставить заявку. Выберите действие: ⬇️",
            reply_markup=reply_markup
        )
        context.user_data['state'] = STATES['START']
        logger.info(f"Приветственное сообщение отправлено пользователю {update.effective_user.id} после принятия заявки")

# Уведомление пользователя о смене статуса
async def notify_user(ticket_id, new_status, context):
    user_id = get_user_id_by_ticket(ticket_id)
    if user_id and new_status == 'Решено':
        keyboard = [
            [InlineKeyboardButton("Отлично 👍 (5/5)", callback_data=f'rate_{ticket_id}_5')],
            [InlineKeyboardButton("Хорошо 👌 (4/5)", callback_data=f'rate_{ticket_id}_4')],
            [InlineKeyboardButton("Нормально 🤔 (3/5)", callback_data=f'rate_{ticket_id}_3')],
            [InlineKeyboardButton("Плохо 👎 (2/5)", callback_data=f'rate_{ticket_id}_2')],
            [InlineKeyboardButton("Ужасно 😞 (1/5)", callback_data=f'rate_{ticket_id}_1')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.user_data['ticket_id'] = ticket_id
        message = await context.bot.send_message(
            chat_id=user_id,
            text=f"Статус вашей заявки #{ticket_id} обновлён: {new_status} 🚀\n"
                 "Пожалуйста, оцените качество поддержки:",
            reply_markup=reply_markup
        )

        # Планируем удаление сообщения с оценкой через 30 секунд
        logger.info(f"Планируем удаление сообщения с оценкой (message_id: {message.message_id}) через 30 секунд")
        context.job_queue.run_once(
            delete_message,
            30,
            context={
                'chat_id': user_id,
                'message_id': message.message_id
            }
        )

# Панель администратора
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    in_progress = get_tickets_by_status('В работе')
    in_progress_text = "📋 Заявки в работе:\n" if in_progress else "📋 Заявки в работе: отсутствуют\n"
    in_progress_keyboard = []
    for i, ticket in enumerate(in_progress, 1):
        in_progress_text += (
            f"{i}. #{ticket[0]} | {ticket[2]}\n"
            f"   UserID: {ticket[1]} | Орг/Отдел: {ticket[3]}\n"
            f"   Имя: {ticket[4]} | Телефон: {ticket[5]}\n"
            f"   Описание: {ticket[6]}\n"
        )
        in_progress_keyboard.append([InlineKeyboardButton("Решено", callback_data=f'status_{ticket[0]}_Решено')])

    resolved = get_tickets_by_status('Решено')
    resolved_text = "✅ Решённые заявки:\n" if resolved else "✅ Решённые заявки: отсутствуют\n"
    for i, ticket in enumerate(resolved, 1):
        rating = get_feedback(ticket[0])
        rating_text = f"Оценка: {rating}/5" if rating is not None else "Оценка: не оставлена"
        resolved_text += (
            f"{i}. #{ticket[0]} | {ticket[2]}\n"
            f"   UserID: {ticket[1]} | Орг/Отдел: {ticket[3]}\n"
            f"   Имя: {ticket[4]} | Телефон: {ticket[5]}\n"
            f"   Описание: {ticket[6]}\n"
            f"   {rating_text}\n"
        )

    accepted = get_tickets_by_status('Принято')
    accepted_text = "📥 Новые заявки:\n" if accepted else "📥 Новые заявки: отсутствуют\n"
    accepted_keyboard = []
    for i, ticket in enumerate(accepted, 1):
        accepted_text += (
            f"{i}. #{ticket[0]} | {ticket[2]}\n"
            f"   UserID: {ticket[1]} | Орг/Отдел: {ticket[3]}\n"
            f"   Имя: {ticket[4]} | Телефон: {ticket[5]}\n"
            f"   Описание: {ticket[6]}\n"
        )
        accepted_keyboard.append([
            InlineKeyboardButton("В работе", callback_data=f'status_{ticket[0]}_В работе'),
            InlineKeyboardButton("Решено", callback_data=f'status_{ticket[0]}_Решено')
        ])

    full_text = f"{accepted_text}\n{in_progress_text}\n{resolved_text}"
    keyboard = accepted_keyboard + in_progress_keyboard + [[InlineKeyboardButton("Обновить 🔄", callback_data='admin_panel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(full_text, reply_markup=reply_markup)

# Проверка на запуск одного экземпляра
def check_single_instance():
    if os.path.exists(LOCK_FILE):
        print("Бот уже запущен! Завершайте предыдущий процесс перед новым запуском.")
        sys.exit(1)
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

# Основная функция
def main():
    check_single_instance()
    try:
        # Инициализация базы данных и добавление администраторов
        init_db()
        add_admin(7186761120)  # Первый администратор
        add_admin(289675630)   # Второй администратор

        # Запуск бота
        application = ApplicationBuilder().token(TOKEN).build()

        application.add_handler(CommandHandler('start', start))
        application.add_handler(CallbackQueryHandler(button_click))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))
        application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        application.add_handler(MessageHandler(filters.ALL & ~(filters.TEXT | filters.COMMAND | filters.CONTACT), handle_media))

        logger.info("Бот запущен!")
        application.run_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
    finally:
        remove_lock()

if __name__ == '__main__':
    main()