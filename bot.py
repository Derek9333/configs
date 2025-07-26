import os
import re
import logging
import tempfile
import base64
import json
import pycountry
import requests
import time
import socket
import concurrent.futures
from urllib.parse import urlparse
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
STRICT_MODE = True  # Режим строгой проверки конфигов
MAX_WORKERS = 5  # Максимальное количество потоков для проверки
MAX_CONFIGS_TO_CHECK = 100  # Максимальное количество конфигов для строгой проверки

# Состояния диалога
WAITING_FILE, WAITING_COUNTRY = range(2)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Кэши
geo_cache = {}
dns_cache = {}

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
        "турция": "turkey", "тайвань": "taiwan", "швейцария": "switzerland",
        "юар": "south africa", "оаэ": "united arab emirates", "саудовская аравия": "saudi arabia",
        "израиль": "israel", "мексика": "mexico", "аргентина": "argentina"
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
    
    logger.info(f"Пользователь {user.id} загрузил файл: {document.file_name} ({document.file_size} байт)")
    await update.message.reply_text(
        "✅ Файл получен. Теперь введите название страны (на русском или английском):"
    )
    return WAITING_COUNTRY

async def handle_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает запрос страны и выдает результаты"""
    user = update.message.from_user
    country_request = update.message.text
    logger.info(f"Пользователь {user.id} запросил страну: {country_request}")
    
    normalized_name = normalize_country_name(country_request)
    logger.info(f"Нормализованное название страны: {normalized_name}")
    
    try:
        # Попытка определить страну через pycountry
        countries = pycountry.countries.search_fuzzy(normalized_name)
        country = countries[0]
        target_country = country.name.lower()
        logger.info(f"Определена страна: {country.name} (целевое название: {target_country})")
        
        # Получаем альтернативные названия и коды стран
        aliases = get_country_aliases(target_country)
        country_codes = [c.alpha_2.lower() for c in countries] + [country.alpha_2.lower()]
        
        logger.info(f"Альтернативы страны: {aliases}, коды: {country_codes}")
    except LookupError:
        logger.warning(f"Страна не распознана: {country_request}")
        await update.message.reply_text("❌ Страна не распознана. Пожалуйста, уточните название.")
        return ConversationHandler.END
    
    # Чтение и обработка файла
    file_path = context.user_data.get('file_path')
    if not file_path or not os.path.exists(file_path):
        logger.error("Путь к файлу не найден или файл отсутствует")
        await update.message.reply_text("❌ Ошибка: файл конфигурации не найден.")
        return ConversationHandler.END
    
    try:
        start_time = time.time()
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            configs = f.read().splitlines()
        logger.info(f"Файл прочитан: {len(configs)} строк, за {time.time()-start_time:.2f} сек")
    except Exception as e:
        logger.error(f"Ошибка чтения файла: {e}")
        await update.message.reply_text("❌ Ошибка обработки файла. Проверьте формат.")
        return ConversationHandler.END
    finally:
        # Удаление временного файла
        if 'file_path' in context.user_data:
            file_path = context.user_data['file_path']
            if os.path.exists(file_path):
                os.unlink(file_path)
            del context.user_data['file_path']
    
    # Поиск релевантных конфигов - этап 1: быстрая фильтрация
    start_time = time.time()
    prelim_configs = []
    for i, config in enumerate(configs):
        if not config.strip():
            continue
        
        try:
            # Быстрая проверка по ключевым словам и доменным зонам
            if is_config_relevant(config, target_country, aliases, country_codes):
                prelim_configs.append(config)
        except Exception as e:
            logger.error(f"Ошибка быстрой проверки конфига #{i}: {e}")
            continue
        
        # Логируем прогресс каждые 1000 конфигов
        if i % 1000 == 0 and i > 0:
            logger.info(f"Обработано {i}/{len(configs)} конфигов...")
    
    logger.info(f"Предварительно найдено {len(prelim_configs)} конфигов для {country.name}, обработка заняла {time.time()-start_time:.2f} сек")
    
    # Если конфигов слишком много, берем только часть для строгой проверки
    if len(prelim_configs) > MAX_CONFIGS_TO_CHECK:
        prelim_configs = prelim_configs[:MAX_CONFIGS_TO_CHECK]
        logger.info(f"Ограничение: для строгой проверки взято {MAX_CONFIGS_TO_CHECK} конфигов")
    
    # Строгая проверка конфигов - этап 2
    strict_matched_configs = []
    if STRICT_MODE and prelim_configs:
        await update.message.reply_text(f"🔍 Начинаю строгую проверку {len(prelim_configs)} конфигов...")
        
        start_time = time.time()
        strict_matched_configs = strict_config_check(prelim_configs, target_country)
        logger.info(f"Строгая проверка завершена: найдено {len(strict_matched_configs)} конфигов, заняло {time.time()-start_time:.2f} сек")
    
    matched_configs = strict_matched_configs if STRICT_MODE else prelim_configs
    
    # Отправка результатов
    if not matched_configs:
        await update.message.reply_text(f"❌ Конфигурации для {country.name} не найдены.")
        return ConversationHandler.END
    
    # Форматирование и отправка с разбивкой на сообщения
    header = f"Конфиги для {country.name} ({len(matched_configs)} шт):\n"
    current_message = header
    sent_messages = 0
    
    for i, config in enumerate(matched_configs):
        config_line = f"{config}\n"
        
        if len(current_message) + len(config_line) > MAX_MSG_LENGTH:
            try:
                await update.message.reply_text(f"<pre>{current_message}</pre>", parse_mode='HTML')
                sent_messages += 1
                current_message = header + config_line
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения: {e}")
                current_message = header + config_line
        else:
            current_message += config_line
        
        # Логируем прогресс каждые 50 конфигов
        if i % 50 == 0 and i > 0:
            logger.info(f"Форматирование: обработано {i}/{len(matched_configs)} конфигов...")
    
    if len(current_message) > len(header):
        try:
            await update.message.reply_text(f"<pre>{current_message}</pre>", parse_mode='HTML')
            sent_messages += 1
        except Exception as e:
            logger.error(f"Ошибка отправки финального сообщения: {e}")
    
    logger.info(f"Отправлено {sent_messages} сообщений с {len(matched_configs)} конфигами для {country.name}")
    await update.message.reply_text(f"✅ Готово! Найдено {len(matched_configs)} конфигов для {country.name}.")
    return ConversationHandler.END

def is_config_relevant(config: str, target_country: str, aliases: list, country_codes: list) -> bool:
    """Быстрая проверка конфига на релевантность стране"""
    # 1. Проверка по ключевым словам
    if detect_by_keywords(config, target_country, aliases):
        return True
    
    # 2. Проверка по доменной зоне
    domain = extract_domain(config)
    if domain:
        tld = domain.split('.')[-1].lower()
        if tld in country_codes:
            return True
    
    # 3. Проверка по геолокации в кэше
    if config in geo_cache and geo_cache[config] == target_country:
        return True
        
    return False

def strict_config_check(configs: list, target_country: str) -> list:
    """Строгая проверка конфигов с геолокацией и проверкой работоспособности"""
    valid_configs = []
    
    # Используем пул потоков для параллельной обработки
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for config in configs:
            futures.append(executor.submit(validate_config, config, target_country))
        
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            config, is_valid = future.result()
            if is_valid:
                valid_configs.append(config)
            
            # Логирование прогресса
            if (i+1) % 10 == 0:
                logger.info(f"Проверено {i+1}/{len(configs)} конфигов...")
    
    return valid_configs

def validate_config(config: str, target_country: str) -> tuple:
    """Проверяет конфиг на валидность и принадлежность к стране"""
    try:
        # Извлечение хоста
        host = extract_host(config)
        if not host:
            return (config, False)
        
        # Проверка DNS (кэширование запросов)
        ip = resolve_dns(host)
        if not ip:
            return (config, False)
        
        # Геолокация IP
        country = geolocate_ip(ip)
        if not country or country.lower() != target_country:
            return (config, False)
        
        # Дополнительная проверка структуры конфига
        if not validate_config_structure(config):
            return (config, False)
            
        return (config, True)
    except Exception as e:
        logger.error(f"Ошибка проверки конфига: {e}")
        return (config, False)

def validate_config_structure(config: str) -> bool:
    """Проверяет базовую структуру конфига"""
    if config.startswith('vmess://'):
        try:
            # Декодирование base64
            encoded = config.split('://')[1].split('?')[0]
            padding = '=' * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded + padding).decode('utf-8', errors='replace')
            json_data = json.loads(decoded)
            
            # Проверка обязательных полей
            required_fields = ['v', 'ps', 'add', 'port', 'id', 'aid']
            return all(field in json_data for field in required_fields)
        except:
            return False
    
    elif config.startswith('vless://'):
        # Проверка формата VLESS
        pattern = r'vless://[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}@'
        return bool(re.match(pattern, config))
    
    # Другие форматы
    return bool(re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b', config))

def resolve_dns(host: str) -> str:
    """Разрешает доменное имя в IP-адрес с кэшированием"""
    if host in dns_cache:
        return dns_cache[host]
    
    try:
        # Пропускаем IP-адреса
        if re.match(r'\d+\.\d+\.\d+\.\d+', host):
            dns_cache[host] = host
            return host
        
        # Разрешение DNS
        ip = socket.gethostbyname(host)
        dns_cache[host] = ip
        return ip
    except:
        return None

def geolocate_ip(ip: str) -> str:
    """Определяет страну по IP с кэшированием"""
    if ip in geo_cache:
        return geo_cache[ip]
    
    try:
        # Пропускаем локальные адреса
        if re.match(r'(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)', ip):
            return None
        
        response = requests.get(f"{GEOIP_API}{ip}", headers=HEADERS, timeout=5)
        data = response.json()
        if data.get('status') == 'success':
            country = data.get('country')
            geo_cache[ip] = country
            return country
    except Exception as e:
        logger.error(f"Ошибка геолокации для {ip}: {e}")
    
    return None

def get_country_aliases(country_name: str) -> list:
    """Возвращает список альтернативных названий для страны"""
    aliases = {
        "united states": ["usa", "us", "сша", "америка", "america", "united states of america"],
        "russia": ["россия", "ru", "rf", "русский", "russian federation"],
        "germany": ["германия", "de", "германи", "deutschland"],
        "united kingdom": ["great britain", "uk", "gb", "англия", "британия", "britain", "england"],
        "france": ["франция", "fr", "french republic"],
        "japan": ["япония", "jp", "японии", "nippon"],
        "brazil": ["бразилия", "br", "brasil"],
        "south korea": ["korea", "southkorea", "sk", "корея", "кр", "republic of korea"],
        "turkey": ["турция", "tr", "турецкий", "türkiye"],
        "taiwan": ["тайвань", "tw", "тайваня", "republic of china"],
        "switzerland": ["швейцария", "ch", "swiss confederation"],
        "china": ["cn", "китай", "chinese", "people's republic of china"],
        "india": ["in", "индия", "bharat"],
        "canada": ["ca", "канада"],
        "australia": ["au", "австралия", "oz"],
        "singapore": ["sg", "сингапур"],
        "italy": ["it", "италия", "italia"]
    }
    return aliases.get(country_name.lower(), [])

def detect_by_keywords(config: str, target_country: str, aliases: list) -> bool:
    """Определение страны по ключевым словам в конфиге"""
    # Словарь паттернов (регулярные выражения с приоритетами)
    patterns = {
        'japan': [r'🇯🇵', r'\bjp\b', r'japan', r'tokyo', r'\.jp\b', r'日本', r'東京'],
        'united states': [r'🇺🇸', r'\bus\b', r'usa\b', r'united states', r'new york', r'\.us\b', r'美国', r'紐約'],
        'russia': [r'🇷🇺', r'\bru\b', r'russia', r'moscow', r'\.ru\b', r'россия', r'俄国', r'москва'],
        'germany': [r'🇩🇪', r'\bde\b', r'germany', r'frankfurt', r'\.de\b', r'германия', r'德国', r'フランクフルト'],
        'united kingdom': [r'🇬🇧', r'\buk\b', r'united kingdom', r'london', r'\.uk\b', r'英国', r'倫敦', r'gb'],
        'france': [r'🇫🇷', r'france', r'paris', r'\.fr\b', r'法国', r'巴黎'],
        'brazil': [r'🇧🇷', r'brazil', r'sao paulo', r'\.br\b', r'巴西', r'聖保羅'],
        'singapore': [r'🇸🇬', r'singapore', r'\.sg\b', r'新加坡', r'星加坡'],
        'south korea': [r'🇰🇷', r'korea', r'seoul', r'\.kr\b', r'韩国', r'首爾', r'korean'],
        'turkey': [r'🇹🇷', r'turkey', r'istanbul', r'\.tr\b', r'土耳其', r'伊斯坦布爾'],
        'taiwan': [r'🇹🇼', r'taiwan', r'taipei', r'\.tw\b', r'台湾', r'台北'],
        'switzerland': [r'🇨🇭', r'switzerland', r'zurich', r'\.ch\b', r'瑞士', r'蘇黎世'],
        'india': [r'🇮🇳', r'india', r'mumbai', r'\.in\b', r'印度', r'孟買'],
        'canada': [r'🇨🇦', r'canada', r'toronto', r'\.ca\b', r'加拿大', r'多倫多'],
        'australia': [r'🇦🇺', r'australia', r'sydney', r'\.au\b', r'澳洲', r'悉尼'],
        'china': [r'🇨🇳', r'china', r'beijing', r'\.cn\b', r'中国', r'北京'],
        'italy': [r'🇮🇹', r'italy', r'rome', r'\.it\b', r'意大利', r'羅馬']
    }
    
    # Создаем список всех ключевых слов для целевой страны
    target_keywords = []
    if target_country in patterns:
        target_keywords = patterns[target_country]
    
    # Добавляем альтернативные названия
    for alias in aliases:
        if alias in patterns:
            target_keywords.extend(patterns[alias])
    
    # Удаляем дубликаты
    target_keywords = list(set(target_keywords))
    
    # Проверяем наличие ключевых слов
    for pattern in target_keywords:
        if re.search(pattern, config, re.IGNORECASE):
            return True
    
    return False

def extract_host(config: str) -> str:
    """Извлекает хост из различных форматов конфигов"""
    # Для VMESS/VLESS ссылок
    if config.startswith(('vmess://', 'vless://')):
        try:
            # Декодирование base64
            encoded = config.split('://')[1].split('?')[0]
            padding = '=' * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded + padding).decode('utf-8', errors='replace')
            json_data = json.loads(decoded)
            host = json_data.get('host') or json_data.get('add', '')
            if host:
                return host
        except Exception as e:
            logger.debug(f"Ошибка декодирования VMESS/VLESS: {e}")
    
    # Для форматов типа host:port
    host_match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', config)
    if host_match:
        return host_match.group(0)
    
    # Для доменных имен
    domain = extract_domain(config)
    if domain:
        return domain
    
    return None

def extract_domain(config: str) -> str:
    """Извлекает доменное имя из конфига"""
    # Поиск доменов в URL
    url_match = re.search(r'(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})', config, re.IGNORECASE)
    if url_match:
        return url_match.group(1)
    
    # Поиск доменов в тексте
    domain_match = re.search(r'\b(?:[a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}\b', config, re.IGNORECASE)
    if domain_match:
        return domain_match.group(0)
    
    return None

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущий диалог"""
    if 'file_path' in context.user_data:
        file_path = context.user_data['file_path']
        if os.path.exists(file_path):
            os.unlink(file_path)
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
        logger.info(f"Запуск в режиме webhook: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url,
            url_path="webhook"
        )
    else:
        # Режим polling для локальной разработки
        logger.info("Запуск в режиме polling")
        application.run_polling()

if __name__ == "__main__":
    main()
