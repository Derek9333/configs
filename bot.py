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
import spacy
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CallbackContext
)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB
MAX_MSG_LENGTH = 4000  # Максимальная длина сообщения с запасом
GEOIP_API = "http://ip-api.com/json/"
HEADERS = {'User-Agent': 'Telegram V2Ray Config Bot/1.0'}
MAX_WORKERS = 5  # Максимальное количество потоков для проверки
CHUNK_SIZE = 100  # Размер сектора для обработки конфигов
MAX_CONFIGS_PER_USER = 5000  # Максимальное количество конфигов на пользователя

# Состояния диалога
WAITING_FILE, WAITING_COUNTRY, WAITING_MODE, SENDING_CONFIGS = range(4)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальная переменная для модели Spacy
nlp_model = None

def load_spacy_model():
    """Загружает модель Spacy для NER при первом использовании"""
    global nlp_model
    if nlp_model is None:
        try:
            nlp_model = spacy.load("en_core_web_sm")
            logger.info("Модель Spacy успешно загружена")
        except Exception as e:
            logger.error(f"Ошибка загрузки модели Spacy: {e}")
    return nlp_model

def normalize_text(text: str) -> str:
    """Нормализует текст, заменяя названия стран на английские эквиваленты"""
    text = text.lower().strip()
    
    # Расширенный словарь замен (русские -> английские)
    ru_en_map = {
        "россия": "russia", "русский": "russia", "рф": "russia", "ру": "russia",
        "сша": "united states", "америка": "united states", "usa": "united states", 
        "us": "united states", "соединенные штаты": "united states",
        "германия": "germany", "дойчланд": "germany", "deutschland": "germany", "де": "germany",
        "япония": "japan", "японии": "japan", "jp": "japan", "яп": "japan",
        "франция": "france", "фр": "france", "франс": "france",
        "великобритания": "united kingdom", "брит": "united kingdom", "англия": "united kingdom", 
        "gb": "united kingdom", "uk": "united kingdom", "гб": "united kingdom",
        "сингапур": "singapore", "sg": "singapore", "синг": "singapore",
        "нидерланды": "netherlands", "голландия": "netherlands", "nl": "netherlands", "нл": "netherlands",
        "канада": "canada", "ca": "canada", "кан": "canada",
        "швейцария": "switzerland", "ch": "switzerland", "швейц": "switzerland",
        "швеция": "sweden", "se": "sweden", "швед": "sweden",
        "австралия": "australia", "оз": "australia", "au": "australia", "австр": "australia",
        "бразилия": "brazil", "br": "brazil", "браз": "brazil",
        "индия": "india", "in": "india", "инд": "india",
        "южная корея": "south korea", "кр": "south korea", "sk": "south korea", 
        "корея": "south korea", "кор": "south korea",
        "турция": "turkey", "tr": "turkey", " тур ": "turkey",
        "тайвань": "taiwan", "tw": "taiwan", "тайв": "taiwan",
        "юар": "south africa", "sa": "south africa", "африка": "south africa",
        "оаэ": "united arab emirates", "эмираты": "united arab emirates", 
        "uae": "united arab emirates", "арабские": "united arab emirates",
        "саудовская аравия": "saudi arabia", "сауд": "saudi arabia", 
        "ksa": "saudi arabia", "саудовская": "saudi arabia",
        "израиль": "israel", "il": "israel", "изр": "israel",
        "мексика": "mexico", "mx": "mexico", "мекс": "mexico",
        "аргентина": "argentina", "ar": "argentina", "арг": "argentina",
        "италия": "italy", "it": "italy", "ит": "italy",
        "испания": "spain", "es": "spain", "исп": "spain",
        "португалия": "portugal", "pt": "portugal", "порт": "portugal",
        "норвегия": "norway", "no": "norway", "норв": "norway",
        "финляндия": "finland", "fi": "finland", "фин": "finland",
        "дания": "denmark", "dk": "denmark", "дан": "denmark",
        "польша": "poland", "pl": "poland", "пол": "poland",
        "украина": "ukraine", "ua": "ukraine", "укр": "ukraine",
        "беларусь": "belarus", "by": "belarus", "бел": "belarus",
        "китай": "china", "cn": "china", "кнр": "china",
        "индонезия": "indonesia", "id": "indonesia", "индо": "indonesia",
        "малайзия": "malaysia", "my": "malaysia", "малай": "malaysia",
        "филиппины": "philippines", "ph": "philippines", "фил": "philippines",
        "вьетнам": "vietnam", "vn": "vietnam", "вьет": "vietnam",
        "тайланд": "thailand", "th": "thailand", "тай": "thailand",
        "чехия": "czech republic", "cz": "czech republic", "чех": "czech republic",
        "румыния": "romania", "ro": "romania", "рум": "romania",
        "венгрия": "hungary", "hu": "hungary", "венг": "hungary",
        "греция": "greece", "gr": "greece", "грец": "greece",
        "болгария": "bulgaria", "bg": "bulgaria", "болг": "bulgaria",
        "египет": "egypt", "eg": "egypt", "егип": "egypt",
        "нигерия": "nigeria", "ng": "nigeria", "нигер": "nigeria",
        "кения": "kenya", "ke": "kenya", "кен": "kenya",
        "колумбия": "colombia", "co": "colombia", "колумб": "colombia",
        "перу": "peru", "pe": "peru",
        "чили": "chile", "cl": "chile",
        "венесуэла": "venezuela", "ve": "venezuela", "венес": "venezuela",
        "австрия": "austria", "at": "austria", "австр": "austria",
        "бельгия": "belgium", "be": "belgium", "бельг": "belgium",
        "ирландия": "ireland", "ie": "ireland", "ирл": "ireland"
    }
    
    # Сортируем ключи по длине (от длинных к коротким)
    sorted_keys = sorted(ru_en_map.keys(), key=len, reverse=True)
    for key in sorted_keys:
        text = text.replace(key, ru_en_map[key])
    
    return text

async def check_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Инициирует процесс проверки конфигов"""
    # Очистка предыдущих данных
    context.user_data.clear()
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
        context.user_data['file_name'] = document.file_name
    
    logger.info(f"Пользователь {user.id} загрузил файл: {document.file_name} ({document.file_size} байт)")
    
    # Предложение загрузить еще файл или перейти к выбору страны
    keyboard = [
        [InlineKeyboardButton("📤 Загрузить еще файл", callback_data='add_file')],
        [InlineKeyboardButton("🌍 Указать страну", callback_data='set_country')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Файл '{document.file_name}' успешно загружен. Вы можете:",
        reply_markup=reply_markup
    )
    return WAITING_COUNTRY

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатия кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_file':
        await query.edit_message_text("Пожалуйста, загрузите дополнительный файл с конфигурациями.")
        return WAITING_FILE
    
    elif query.data == 'set_country':
        await query.edit_message_text("Введите название страны (на русском или английском):")
        return WAITING_COUNTRY
    
    elif query.data == 'fast_mode':
        context.user_data['search_mode'] = 'fast'
        await process_search(update, context)
        return SENDING_CONFIGS
    
    elif query.data == 'strict_mode':
        context.user_data['search_mode'] = 'strict'
        await process_search(update, context)
        return SENDING_CONFIGS
    
    elif query.data == 'stop_sending':
        context.user_data['stop_sending'] = True
        await query.edit_message_text("⏹ Отправка конфигов остановлена.")
        return ConversationHandler.END
    
    return WAITING_COUNTRY

async def handle_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает запрос страны"""
    country_request = update.message.text
    context.user_data['country_request'] = country_request
    
    # Предложение выбора режима поиска
    keyboard = [
        [
            InlineKeyboardButton("⚡ Быстрый поиск", callback_data='fast_mode'),
            InlineKeyboardButton("🔍 Строгий поиск", callback_data='strict_mode')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Вы выбрали страну: {country_request}\nВыберите режим поиска:",
        reply_markup=reply_markup
    )
    return WAITING_MODE

async def process_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает поиск конфигов в выбранном режиме"""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.message.from_user.id
    
    country_request = context.user_data.get('country_request', '')
    search_mode = context.user_data.get('search_mode', 'fast')
    
    if not country_request:
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка: страна не указана.")
        return ConversationHandler.END
    
    logger.info(f"Пользователь {user_id} запросил страну: {country_request} в режиме {search_mode}")
    
    normalized_text = normalize_text(country_request)
    logger.info(f"Нормализованный текст: {normalized_text}")
    
    try:
        # Попытка определить страну через pycountry
        countries = pycountry.countries.search_fuzzy(normalized_text)
        country = countries[0]
        target_country = country.name.lower()
        logger.info(f"Определена страна: {country.name} (целевое название: {target_country})")
        
        # Получаем альтернативные названия и коды стран
        aliases = get_country_aliases(target_country)
        country_codes = [c.alpha_2.lower() for c in countries] + [country.alpha_2.lower()]
        
        logger.info(f"Альтернативы страны: {aliases}, коды: {country_codes}")
    except LookupError:
        logger.warning(f"Страна не распознана: {country_request}")
        # Попытка извлечь страну через NER
        nlp = load_spacy_model()
        if nlp:
            try:
                doc = nlp(normalized_text)
                logger.info(f"Извлеченные сущности: {[(ent.text, ent.label_) for ent in doc.ents]}")
                
                found_countries = []
                for ent in doc.ents:
                    if ent.label_ in ['GPE', 'COUNTRY']:
                        try:
                            # Ищем страну по названию сущности
                            countries_list = pycountry.countries.search_fuzzy(ent.text)
                            if countries_list:
                                country_obj = countries_list[0]
                                found_countries.append(country_obj.name)
                                logger.info(f"Найдена страна через NER: {ent.text} -> {country_obj.name}")
                        except LookupError:
                            continue
                
                if found_countries:
                    # Убираем дубликаты, сохраняя порядок
                    seen = set()
                    unique_countries = [c for c in found_countries if c not in seen and not seen.add(c)]
                    
                    if len(unique_countries) == 1:
                        country_name = unique_countries[0]
                    else:
                        # Если несколько стран, выбираем первую
                        country_name = unique_countries[0]
                    
                    # Получаем объект страны
                    country = pycountry.countries.search_fuzzy(country_name)[0]
                    target_country = country.name.lower()
                    aliases = get_country_aliases(target_country)
                    country_codes = [country.alpha_2.lower()]
                    logger.info(f"Страна определена через NER: {country.name}")
                else:
                    logger.warning("Не удалось определить страну через NER")
                    await context.bot.send_message(chat_id=user_id, text="❌ Страна не распознана. Пожалуйста, уточните название.")
                    return ConversationHandler.END
            except Exception as e:
                logger.error(f"Ошибка NER: {e}")
                await context.bot.send_message(chat_id=user_id, text="❌ Ошибка обработки запроса. Попробуйте еще раз.")
                return ConversationHandler.END
        else:
            await context.bot.send_message(chat_id=user_id, text="❌ Ошибка обработки запроса. Попробуйте еще раз.")
            return ConversationHandler.END
    
    # Сохраняем данные о стране для использования
    context.user_data['country'] = country.name
    context.user_data['target_country'] = target_country
    context.user_data['aliases'] = aliases
    context.user_data['country_codes'] = country_codes
    
    # Чтение и обработка файлов
    file_paths = context.user_data.get('file_paths', [])
    if not file_paths:
        # Если файлов еще нет, используем текущий
        if 'file_path' in context.user_data:
            file_paths = [context.user_data['file_path']]
            context.user_data['file_paths'] = file_paths
    
    if not file_paths:
        logger.error("Пути к файлам не найдены")
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка: файлы конфигурации не найдены.")
        return ConversationHandler.END
    
    # Чтение всех файлов
    all_configs = []
    for file_path in file_paths:
        try:
            await context.bot.send_message(chat_id=user_id, text="⏳ Идет чтение файла...")
            start_time = time.time()
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                configs = f.read().splitlines()
            logger.info(f"Файл прочитан: {len(configs)} строк, за {time.time()-start_time:.2f} сек")
            all_configs.extend(configs)
        except Exception as e:
            logger.error(f"Ошибка чтения файла: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"❌ Ошибка обработки файла: {e}")
    
    if not all_configs:
        await context.bot.send_message(chat_id=user_id, text="❌ В файлах не найдено конфигураций.")
        return ConversationHandler.END
    
    # Ограничение количества конфигов
    if len(all_configs) > MAX_CONFIGS_PER_USER:
        all_configs = all_configs[:MAX_CONFIGS_PER_USER]
        await context.bot.send_message(
            chat_id=user_id, 
            text=f"⚠️ Внимание: обрабатывается только первые {MAX_CONFIGS_PER_USER} конфигов из-за ограничений."
        )
    
    context.user_data['all_configs'] = all_configs
    
    # Быстрый поиск
    if search_mode == 'fast':
        await context.bot.send_message(chat_id=user_id, text="🔎 Начинаю быстрый поиск конфигов...")
        await fast_search(update, context)
    # Строгий поиск
    else:
        await context.bot.send_message(chat_id=user_id, text="🔍 Начинаю строгий поиск конфигов...")
        await strict_search(update, context)
    
    return SENDING_CONFIGS

async def fast_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет быстрый поиск конфигов"""
    user_id = update.callback_query.from_user.id if update.callback_query else update.message.from_user.id
    all_configs = context.user_data.get('all_configs', [])
    target_country = context.user_data.get('target_country', '')
    aliases = context.user_data.get('aliases', [])
    country_codes = context.user_data.get('country_codes', [])
    
    if not all_configs or not target_country:
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка: данные для поиска отсутствуют.")
        return ConversationHandler.END
    
    # Поиск релевантных конфигов
    start_time = time.time()
    matched_configs = []
    
    # Получаем флаг страны для поиска в конфигах
    flag_pattern = get_country_flag_pattern(context.user_data['country'])
    
    for i, config in enumerate(all_configs):
        if not config.strip():
            continue
        
        try:
            # Проверка по ключевым словам и доменным зонам
            if is_config_relevant(config, target_country, aliases, country_codes, flag_pattern):
                matched_configs.append(config)
        except Exception as e:
            logger.error(f"Ошибка проверки конфига #{i}: {e}")
            continue
        
        # Логируем прогресс каждые 1000 конфигов
        if i % 1000 == 0 and i > 0:
            logger.info(f"Обработано {i}/{len(all_configs)} конфигов...")
    
    logger.info(f"Найдено {len(matched_configs)} конфигов для {context.user_data['country']}, обработка заняла {time.time()-start_time:.2f} сек")
    
    if not matched_configs:
        await context.bot.send_message(chat_id=user_id, text=f"❌ Конфигурации для {context.user_data['country']} не найдены.")
        return ConversationHandler.END
    
    context.user_data['matched_configs'] = matched_configs
    context.user_data['current_index'] = 0
    context.user_data['stop_sending'] = False
    
    # Начинаем отправку конфигов
    await send_configs(update, context)

async def strict_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет строгий поиск конфигов"""
    user_id = update.callback_query.from_user.id if update.callback_query else update.message.from_user.id
    all_configs = context.user_data.get('all_configs', [])
    target_country = context.user_data.get('target_country', '')
    
    if not all_configs or not target_country:
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка: данные для поиска отсутствуют.")
        return ConversationHandler.END
    
    # Предварительная фильтрация
    await context.bot.send_message(chat_id=user_id, text="🔎 Этап 1: предварительная фильтрация конфигов...")
    start_time = time.time()
    prelim_configs = []
    
    # Получаем флаг страны для поиска в конфигах
    flag_pattern = get_country_flag_pattern(context.user_data['country'])
    
    for i, config in enumerate(all_configs):
        if not config.strip():
            continue
        
        try:
            # Быстрая проверка по ключевым словам и доменным зонам
            if is_config_relevant(config, target_country, context.user_data['aliases'], context.user_data['country_codes'], flag_pattern):
                prelim_configs.append(config)
        except Exception as e:
            logger.error(f"Ошибка быстрой проверки конфига #{i}: {e}")
            continue
        
        # Логируем прогресс каждые 1000 конфигов
        if i % 1000 == 0 and i > 0:
            logger.info(f"Обработано {i}/{len(all_configs)} конфигов...")
    
    logger.info(f"Предварительно найдено {len(prelim_configs)} конфигов для {context.user_data['country']}, обработка заняла {time.time()-start_time:.2f} сек")
    
    if not prelim_configs:
        await context.bot.send_message(chat_id=user_id, text=f"❌ Конфигурации для {context.user_data['country']} не найдены.")
        return ConversationHandler.END
    
    # Строгая проверка конфигов
    total_chunks = (len(prelim_configs) + CHUNK_SIZE - 1) // CHUNK_SIZE
    await context.bot.send_message(
        chat_id=user_id,
        text=f"🔍 Начинаю строгую проверку {len(prelim_configs)} конфигов секторами по {CHUNK_SIZE}...\n"
        f"Всего секторов: {total_chunks}"
    )
    
    start_time = time.time()
    strict_matched_configs = []
    
    for chunk_idx in range(0, len(prelim_configs), CHUNK_SIZE):
        if context.user_data.get('stop_sending'):
            break
            
        chunk = prelim_configs[chunk_idx:chunk_idx + CHUNK_SIZE]
        chunk_start_time = time.time()
        
        # Проверяем текущий сектор
        valid_configs = strict_config_check(chunk, target_country)
        strict_matched_configs.extend(valid_configs)
        
        # Отправляем промежуточные результаты
        chunk_end_time = time.time()
        chunk_time = chunk_end_time - chunk_start_time
        
        if chunk_idx + CHUNK_SIZE < len(prelim_configs):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Сектор {chunk_idx//CHUNK_SIZE + 1}/{total_chunks} обработан\n"
                f"Найдено конфигов: {len(valid_configs)}\n"
                f"Время обработки: {chunk_time:.1f} сек\n"
                f"Всего найдено: {len(strict_matched_configs)}"
            )
        
        # Отправляем сами конфиги, если они есть
        if valid_configs:
            context.user_data['matched_configs'] = valid_configs
            context.user_data['current_index'] = 0
            await send_configs(update, context)
    
    total_time = time.time() - start_time
    logger.info(f"Строгая проверка завершена: найдено {len(strict_matched_configs)} конфигов, заняло {total_time:.2f} сек")
    
    if not strict_matched_configs:
        await context.bot.send_message(chat_id=user_id, text=f"❌ Конфигурации для {context.user_data['country']} не найдены.")
        return ConversationHandler.END
    
    # Отправляем все оставшиеся конфиги
    context.user_data['matched_configs'] = strict_matched_configs
    context.user_data['current_index'] = 0
    context.user_data['stop_sending'] = False
    await send_configs(update, context)

async def send_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет конфиги пользователю с разбивкой на сообщения"""
    user_id = update.callback_query.from_user.id if update.callback_query else update.message.from_user.id
    matched_configs = context.user_data.get('matched_configs', [])
    current_index = context.user_data.get('current_index', 0)
    country_name = context.user_data.get('country', '')
    stop_sending = context.user_data.get('stop_sending', False)
    
    if not matched_configs or current_index >= len(matched_configs) or stop_sending:
        if not stop_sending and current_index < len(matched_configs):
            await context.bot.send_message(chat_id=user_id, text="✅ Все конфиги отправлены.")
        return ConversationHandler.END
    
    # Кнопка остановки
    stop_button = [[InlineKeyboardButton("⏹ Остановить отправку", callback_data='stop_sending')]]
    reply_markup = InlineKeyboardMarkup(stop_button)
    
    # Формируем сообщение
    header = f"Конфиги для {country_name}:\n\n"
    message = header
    sent_count = 0
    
    # Получаем флаг страны для добавления к конфигам
    flag = get_country_flag(context.user_data['country'])
    
    while current_index < len(matched_configs):
        config = matched_configs[current_index]
        config_line = f"{flag} {config}\n\n" if flag else f"{config}\n\n"
        
        # Проверяем, не превысит ли добавление новой строки лимит
        if len(message) + len(config_line) > MAX_MSG_LENGTH:
            break
            
        message += config_line
        current_index += 1
        sent_count += 1
        
        # Ограничиваем количество конфигов в одном сообщении
        if sent_count >= 20:
            break
    
    # Отправляем сообщение
    try:
        if message.strip() != header.strip():
            await context.bot.send_message(
                chat_id=user_id,
                text=f"<pre>{message}</pre>",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
    
    # Сохраняем текущую позицию
    context.user_data['current_index'] = current_index
    
    # Если еще есть конфиги для отправки, планируем следующее сообщение
    if current_index < len(matched_configs) and not context.user_data.get('stop_sending', False):
        # Даем небольшую паузу между сообщениями
        time.sleep(1)
        await send_configs(update, context)
    else:
        if current_index >= len(matched_configs):
            await context.bot.send_message(chat_id=user_id, text="✅ Все конфиги отправлены.")
        return ConversationHandler.END

def is_config_relevant(config: str, target_country: str, aliases: list, country_codes: list, flag_pattern: str = None) -> bool:
    """Быстрая проверка конфига на релевантность стране"""
    # 1. Проверка по флагу (если указан)
    if flag_pattern and re.search(flag_pattern, config, re.IGNORECASE):
        return True
    
    # 2. Проверка по ключевым словам
    if detect_by_keywords(config, target_country, aliases):
        return True
    
    # 3. Проверка по доменной зоне
    domain = extract_domain(config)
    if domain:
        tld = domain.split('.')[-1].lower()
        if tld in country_codes:
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
        
        for future in concurrent.futures.as_completed(futures):
            config, is_valid = future.result()
            if is_valid:
                valid_configs.append(config)
    
    return valid_configs

def validate_config(config: str, target_country: str) -> tuple:
    """Проверяет конфиг на валидность и принадлежность к стране"""
    try:
        # Извлечение хоста
        host = extract_host(config)
        if not host:
            return (config, False)
        
        # Проверка DNS
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
    """Разрешает доменное имя в IP-адрес"""
    try:
        # Пропускаем IP-адреса
        if re.match(r'\d+\.\d+\.\d+\.\d+', host):
            return host
        
        # Разрешение DNS
        return socket.gethostbyname(host)
    except:
        return None

def geolocate_ip(ip: str) -> str:
    """Определяет страну по IP"""
    try:
        # Пропускаем локальные адреса
        if re.match(r'(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)', ip):
            return None
        
        response = requests.get(f"{GEOIP_API}{ip}", headers=HEADERS, timeout=5)
        data = response.json()
        if data.get('status') == 'success':
            return data.get('country')
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
        "italy": ["it", "италия", "italia"],
        "spain": ["es", "испания"],
        "portugal": ["pt", "португалия"],
        "norway": ["no", "норвегия"],
        "finland": ["fi", "финляндия"],
        "denmark": ["dk", "дания"],
        "poland": ["pl", "польша"],
        "ukraine": ["ua", "украина"],
        "belarus": ["by", "беларусь"],
        "indonesia": ["id", "индонезия"],
        "malaysia": ["my", "малайзия"],
        "philippines": ["ph", "филиппины"],
        "vietnam": ["vn", "вьетнам"],
        "thailand": ["th", "тайланд"],
        "czech republic": ["cz", "чехия"],
        "romania": ["ro", "румыния"],
        "hungary": ["hu", "венгрия"],
        "greece": ["gr", "греция"],
        "bulgaria": ["bg", "болгария"],
        "egypt": ["eg", "египет"],
        "nigeria": ["ng", "нигерия"],
        "kenya": ["ke", "кения"],
        "colombia": ["co", "колумбия"],
        "peru": ["pe", "перу"],
        "chile": ["cl", "чили"],
        "venezuela": ["ve", "венесуэла"],
        "austria": ["at", "австрия"],
        "belgium": ["be", "бельгия"],
        "ireland": ["ie", "ирландия"]
    }
    return aliases.get(country_name.lower(), [])

def get_country_flag(country_name: str) -> str:
    """Возвращает флаг страны для вставки в конфиг"""
    flag_map = {
        "united states": "🇺🇸",
        "russia": "🇷🇺",
        "germany": "🇩🇪",
        "united kingdom": "🇬🇧",
        "france": "🇫🇷",
        "japan": "🇯🇵",
        "brazil": "🇧🇷",
        "south korea": "🇰🇷",
        "turkey": "🇹🇷",
        "taiwan": "🇹🇼",
        "switzerland": "🇨🇭",
        "china": "🇨🇳",
        "india": "🇮🇳",
        "canada": "🇨🇦",
        "australia": "🇦🇺",
        "singapore": "🇸🇬",
        "italy": "🇮🇹",
        "spain": "🇪🇸",
        "portugal": "🇵🇹",
        "norway": "🇳🇴",
        "finland": "🇫🇮",
        "denmark": "🇩🇰",
        "poland": "🇵🇱",
        "ukraine": "🇺🇦",
        "belarus": "🇧🇾",
        "indonesia": "🇮🇩",
        "malaysia": "🇲🇾",
        "philippines": "🇵🇭",
        "vietnam": "🇻🇳",
        "thailand": "🇹🇭",
        "czech republic": "🇨🇿",
        "romania": "🇷🇴",
        "hungary": "🇭🇺",
        "greece": "🇬🇷",
        "bulgaria": "🇧🇬",
        "egypt": "🇪🇬",
        "nigeria": "🇳🇬",
        "kenya": "🇰🇪",
        "colombia": "🇨🇴",
        "peru": "🇵🇪",
        "chile": "🇨🇱",
        "venezuela": "🇻🇪",
        "austria": "🇦🇹",
        "belgium": "🇧🇪",
        "ireland": "🇮🇪"
    }
    return flag_map.get(country_name.lower(), "")

def get_country_flag_pattern(country_name: str) -> str:
    """Возвращает регулярное выражение для поиска флага страны"""
    flag = get_country_flag(country_name)
    if flag:
        # Экранируем специальные символы в флаге
        return re.escape(flag)
    return ""

def detect_by_keywords(config: str, target_country: str, aliases: list) -> bool:
    """Определение страны по ключевым словам в конфиге"""
    # Словарь паттернов (регулярные выражения с приоритетами)
    patterns = {
        'japan': [r'jp\b', r'japan', r'tokyo', r'\.jp\b', r'日本', r'東京'],
        'united states': [r'us\b', r'usa\b', r'united states', r'new york', r'\.us\b', r'美国', r'紐約'],
        'russia': [r'ru\b', r'russia', r'moscow', r'\.ru\b', r'россия', r'俄国', r'москва'],
        'germany': [r'de\b', r'germany', r'frankfurt', r'\.de\b', r'германия', r'德国', r'フランクフルト'],
        'united kingdom': [r'uk\b', r'united kingdom', r'london', r'\.uk\b', r'英国', r'倫敦', r'gb'],
        'france': [r'france', r'paris', r'\.fr\b', r'法国', r'巴黎'],
        'brazil': [r'brazil', r'sao paulo', r'\.br\b', r'巴西', r'聖保羅'],
        'singapore': [r'singapore', r'\.sg\b', r'新加坡', r'星加坡'],
        'south korea': [r'korea', r'seoul', r'\.kr\b', r'韩国', r'首爾', r'korean'],
        'turkey': [r'turkey', r'istanbul', r'\.tr\b', r'土耳其', r'伊斯坦布爾'],
        'taiwan': [r'taiwan', r'taipei', r'\.tw\b', r'台湾', r'台北'],
        'switzerland': [r'switzerland', r'zurich', r'\.ch\b', r'瑞士', r'蘇黎世'],
        'india': [r'india', r'mumbai', r'\.in\b', r'印度', r'孟買'],
        'canada': [r'canada', r'toronto', r'\.ca\b', r'加拿大', r'多倫多'],
        'australia': [r'australia', r'sydney', r'\.au\b', r'澳洲', r'悉尼'],
        'china': [r'china', r'beijing', r'\.cn\b', r'中国', r'北京'],
        'italy': [r'italy', r'rome', r'\.it\b', r'意大利', r'羅馬'],
        'spain': [r'spain', r'madrid', r'\.es\b', r'西班牙', r'马德里'],
        'portugal': [r'portugal', r'lisbon', r'\.pt\b', r'葡萄牙', r'里斯本'],
        'norway': [r'norway', r'oslo', r'\.no\b', r'挪威', r'奥斯陆'],
        'finland': [r'finland', r'helsinki', r'\.fi\b', r'芬兰', r'赫尔辛基'],
        'denmark': [r'denmark', r'copenhagen', r'\.dk\b', r'丹麦', r'哥本哈根'],
        'poland': [r'poland', r'warsaw', r'\.pl\b', r'波兰', r'华沙'],
        'ukraine': [r'ukraine', r'kyiv', r'\.ua\b', r'乌克兰', r'基辅'],
        'belarus': [r'belarus', r'minsk', r'\.by\b', r'白俄罗斯', r'明斯克'],
        'indonesia': [r'indonesia', r'jakarta', r'\.id\b', r'印度尼西亚', r'雅加达'],
        'malaysia': [r'malaysia', r'kuala lumpur', r'\.my\b', r'马来西亚', r'吉隆坡'],
        'philippines': [r'philippines', r'manila', r'\.ph\b', r'菲律宾', r'马尼拉'],
        'vietnam': [r'vietnam', r'hanoi', r'\.vn\b', r'越南', r'河内'],
        'thailand': [r'thailand', r'bangkok', r'\.th\b', r'泰国', r'曼谷'],
        'czech republic': [r'czech', r'prague', r'\.cz\b', r'捷克', r'布拉格'],
        'romania': [r'romania', r'bucharest', r'\.ro\b', r'罗马尼亚', r'布加勒斯特'],
        'hungary': [r'hungary', r'budapest', r'\.hu\b', r'匈牙利', r'布达佩斯'],
        'greece': [r'greece', r'athens', r'\.gr\b', r'希腊', r'雅典'],
        'bulgaria': [r'bulgaria', r'sofia', r'\.bg\b', r'保加利亚', r'索非亚'],
        'egypt': [r'egypt', r'cairo', r'\.eg\b', r'埃及', r'开罗'],
        'nigeria': [r'nigeria', r'abuja', r'\.ng\b', r'尼日利亚', r'阿布贾'],
        'kenya': [r'kenya', r'nairobi', r'\.ke\b', r'肯尼亚', r'内罗毕'],
        'colombia': [r'colombia', r'bogota', r'\.co\b', r'哥伦比亚', r'波哥大'],
        'peru': [r'peru', r'lima', r'\.pe\b', r'秘鲁', r'利马'],
        'chile': [r'chile', r'santiago', r'\.cl\b', r'智利', r'圣地亚哥'],
        'venezuela': [r'venezuela', r'caracas', r'\.ve\b', r'委内瑞拉', r'加拉加斯'],
        'austria': [r'austria', r'vienna', r'\.at\b', r'奥地利', r'维也纳'],
        'belgium': [r'belgium', r'brussels', r'\.be\b', r'比利时', r'布鲁塞尔'],
        'ireland': [r'ireland', r'dublin', r'\.ie\b', r'爱尔兰', r'都柏林']
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
    # Очищаем временные файлы
    for key in ['file_path', 'file_paths']:
        if key in context.user_data:
            file_paths = context.user_data[key]
            if not isinstance(file_paths, list):
                file_paths = [file_paths]
                
            for file_path in file_paths:
                if os.path.exists(file_path):
                    os.unlink(file_path)
            del context.user_data[key]
    
    context.user_data.clear()
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
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country)
            ],
            WAITING_MODE: [
                CallbackQueryHandler(button_handler)
            ],
            SENDING_CONFIGS: [
                CallbackQueryHandler(button_handler)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_user=True
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
