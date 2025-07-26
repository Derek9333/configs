import os
import re
import logging
import tempfile
import base64
import json
import pycountry
import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB
MAX_MSG_LENGTH = 4000  # Максимальная длина сообщения с запасом
GEOIP_API = "http://ip-api.com/json/"
HEADERS = {'User-Agent': 'Telegram V2Ray Config Bot/1.0'}

# Состояния диалога
WAITING_FILE, WAITING_COUNTRY = range(2)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Кэш для геолокации
geo_cache = {}

def normalize_country_name(name: str) -> str:
    """Нормализует название страны для сопоставления"""
    name = name.lower().strip()
    
    # Замена русских названий на английские
    ru_en_map = {
        "россия": "russia", "сша": "united states", "германия": "germany",
        "япония": "japan", "франция": "france", "великобритания": "united kingdom",
        "сингапур": "singapore", "нидерланды": "netherlands", "канада": "canada",
        "швейцария": "switzerland", "швеция": "sweden", "австралия": "australia",
        "бразилия": "brazil", "индия": "india", "южная корея": "south korea",
        "турция": "turkey", "тайвань": "taiwan", "швейцария": "switzerland"
    }
    return ru_en_map.get(name, name)

async def check_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Инициирует процесс проверки конфигов"""
    await update.message.reply_text(
        "Пожалуйста, загрузите текстовый файл с конфигурациями V2RayTun."
    )
    return WAITING_FILE

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает загруженный файл"""
    user = update.message.from_user
    document = update.message.document
    
    # Проверка типа файла
    if not document.mime_type.startswith('text/'):
        await update.message.reply_text("❌ Пожалуйста, загрузите текстовый файл.")
        return ConversationHandler.END
    
    # Проверка размера файла
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(
            f"❌ Файл слишком большой. Максимальный размер: {MAX_FILE_SIZE//1024//1024}MB"
        )
        return ConversationHandler.END
    
    # Скачивание файла во временное хранилище
    file = await context.bot.get_file(document.file_id)
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        await file.download_to_memory(tmp_file)
        context.user_data['file_path'] = tmp_file.name
    
    logger.info(f"Пользователь {user.id} загрузил файл: {document.file_name}")
    await update.message.reply_text(
        "✅ Файл получен. Теперь введите название страны (на русском или английском):"
    )
    return WAITING_COUNTRY

async def handle_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает запрос страны и выдает результаты"""
    country_request = update.message.text
    normalized_name = normalize_country_name(country_request)
    
    try:
        # Попытка определить страну через pycountry
        country = pycountry.countries.search_fuzzy(normalized_name)[0]
        target_country = country.name.lower()
    except LookupError:
        await update.message.reply_text("❌ Страна не распознана. Пожалуйста, уточните название.")
        return ConversationHandler.END
    
    # Чтение и обработка файла
    try:
        with open(context.user_data['file_path'], 'r', encoding='utf-8') as f:
            configs = f.read().splitlines()
    except Exception as e:
        logger.error(f"Ошибка чтения файла: {e}")
        await update.message.reply_text("❌ Ошибка обработки файла. Проверьте формат.")
        return ConversationHandler.END
    finally:
        # Удаление временного файла
        if 'file_path' in context.user_data:
            if os.path.exists(context.user_data['file_path']):
                os.unlink(context.user_data['file_path'])
            del context.user_data['file_path']
    
    # Поиск релевантных конфигов
    matched_configs = []
    for config in configs:
        if not config.strip():
            continue
        
        config_country = identify_country(config)
        if config_country and target_country == config_country:
            matched_configs.append(config)
    
    # Отправка результатов
    if not matched_configs:
        await update.message.reply_text(f"❌ Конфигурации для {country.name} не найдены.")
        return ConversationHandler.END
    
    # Форматирование и отправка с разбивкой на сообщения
    header = f"конфиги: {country.name}\n"
    current_message = header
    
    for config in matched_configs:
        config_line = f"{config}\n"
        
        if len(current_message) + len(config_line) > MAX_MSG_LENGTH:
            await update.message.reply_text(f"```\n{current_message}\n```", parse_mode='MarkdownV2')
            current_message = header + config_line
        else:
            current_message += config_line
    
    if len(current_message) > len(header):
        await update.message.reply_text(f"```\n{current_message}\n```", parse_mode='MarkdownV2')
    
    logger.info(f"Отправлено {len(matched_configs)} конфигов для {country.name}")
    return ConversationHandler.END

def identify_country(config: str) -> str:
    """Определяет страну для конфигурации с использованием нескольких методов"""
    # Проверка кэша
    if config in geo_cache:
        return geo_cache[config]
    
    # Метод 1: Поиск по ключевым словам и эмодзи
    country_match = detect_by_keywords(config)
    if country_match:
        geo_cache[config] = country_match
        return country_match
    
    # Метод 2: Извлечение IP/домена
    host = extract_host(config)
    if not host:
        return None
    
    # Метод 3: Геолокация по IP
    country_name = geolocate_host(host)
    geo_cache[config] = country_name
    return country_name

def detect_by_keywords(config: str) -> str:
    """Определение страны по ключевым словам в конфиге"""
    # Словарь паттернов (регулярные выражения с приоритетами)
    patterns = {
        'japan': [r'🇯🇵', r'\bjp\b', r'japan', r'tokyo', r'\.jp\b', r'日本'],
        'united states': [r'🇺🇸', r'\bus\b', r'usa\b', r'united states', r'new york', r'\.us\b', r'美国'],
        'russia': [r'🇷🇺', r'\bru\b', r'russia', r'moscow', r'\.ru\b', r'россия', r'俄国'],
        'germany': [r'🇩🇪', r'\bde\b', r'germany', r'frankfurt', r'\.de\b', r'германия', r'德国'],
        'united kingdom': [r'🇬🇧', r'\buk\b', r'united kingdom', r'london', r'\.uk\b', r'英国'],
        'france': [r'🇫🇷', r'france', r'paris', r'\.fr\b', r'法国'],
        'brazil': [r'🇧🇷', r'brazil', r'sao paulo', r'\.br\b', r'巴西'],
        'singapore': [r'🇸🇬', r'singapore', r'\.sg\b', r'新加坡'],
        'south korea': [r'🇰🇷', r'korea', r'seoul', r'\.kr\b', r'韩国'],
        'turkey': [r'🇹🇷', r'turkey', r'istanbul', r'\.tr\b', r'土耳其'],
        'taiwan': [r'🇹🇼', r'taiwan', r'taipei', r'\.tw\b', r'台湾'],
        'switzerland': [r'🇨🇭', r'switzerland', r'zurich', r'\.ch\b', r'瑞士']
    }
    
    # Проверка по убыванию приоритета
    for country, regex_list in patterns.items():
        for pattern in regex_list:
            if re.search(pattern, config, re.IGNORECASE):
                return country
    
    return None

def extract_host(config: str) -> str:
    """Извлекает хост из различных форматов конфигов"""
    # Для VMESS/VLESS ссылок
    if config.startswith(('vmess://', 'vless://')):
        try:
            # Декодирование base64
            encoded = config.split('://')[1].split('?')[0]
            padding = '=' * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded + padding).decode('utf-8')
            json_data = json.loads(decoded)
            return json_data.get('host') or json_data.get('add', '')
        except Exception:
            pass
    
    # Для форматов типа host:port
    host_match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', config)
    if host_match:
        return host_match.group(0)
    
    # Для доменных имен
    domain_match = re.search(r'\b(?:[a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}\b', config, re.IGNORECASE)
    if domain_match:
        return domain_match.group(0)
    
    return None

def geolocate_host(host: str) -> str:
    """Определяет страну по хосту через API"""
    try:
        # Пропускаем локальные адреса
        if re.match(r'(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)', host):
            return None
        
        response = requests.get(f"{GEOIP_API}{host}", headers=HEADERS, timeout=3)
        data = response.json()
        if data.get('status') == 'success':
            return data.get('country', '').lower()
    except Exception:
        pass
    return None

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущий диалог"""
    if 'file_path' in context.user_data:
        if os.path.exists(context.user_data['file_path']):
            os.unlink(context.user_data['file_path'])
        del context.user_data['file_path']
    
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

def main() -> None:
    """Запуск бота"""
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("check_configs", check_configs)],
        states={
            WAITING_FILE: [
                MessageHandler(filters.Document.TEXT, handle_document),
                MessageHandler(filters.ALL & ~filters.COMMAND, 
                              lambda u, c: u.message.reply_text("❌ Пожалуйста, загрузите текстовый файл."))
            ],
            WAITING_COUNTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    
    # Настройка веб-сервера для Render.com
    port = int(os.environ.get('PORT', 5000))
    external_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    
    if external_host:
        # Режим вебхуков для Render.com
        webhook_url = f"https://{external_host}/webhook"
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url,
            url_path="webhook"
        )
    else:
        # Режим polling для локальной разработки
        application.run_polling()

if __name__ == "__main__":
    main()
