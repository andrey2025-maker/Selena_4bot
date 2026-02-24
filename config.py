import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Токен бота (единственная переменная из .env)
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # ID канала-источника уведомлений
    SOURCE_CHANNEL_ID = int(os.getenv("SOURCE_CHANNEL_ID", -1003291808303))

    # ID группы для обязательной подписки
    REQUIRED_GROUP_ID = int(os.getenv("REQUIRED_GROUP_ID", -1002927295087))

    # ID главного администратора
    ADMIN_ID = int(os.getenv("ADMIN_ID", 1835558263))
    
    # Настройки базы данных
    DATABASE_PATH = "database.db"
    
    # Английские названия фруктов (без @)
    AVAILABLE_FRUITS_EN = [
        "Pear", "Pineapple", "Gold Mango", "Dragon Fruit", 
        "Bloodstone Cycad", "Colossal Pinecone", "Franken Kiwi",
        "Pumpkin", "Durian", "Candy Corn", "Deepsea Pearl Fruit",
        "Volt Ginkgo", "Cranberry", "Acorn", "Gingerbread", "Candycane", "Cherry"
    ]
    
    # Русские названия фруктов (переводы)
    FRUIT_TRANSLATIONS = {
        # Английское: Русское
        "Pear": "Груша",
        "Pineapple": "Ананас",
        "Gold Mango": "Манго",
        "Dragon Fruit": "Драконий фрукт",
        "Bloodstone Cycad": "Bloodstone Cycad",
        "Colossal Pinecone": "Colossal Pinecone",
        "Franken Kiwi": "Франкен Киви",
        "Pumpkin": "Тыква",
        "Durian": "Дуриан",
        "Candy Corn": "Конфета",
        "Deepsea Pearl Fruit": "Ракушка",
        "Volt Ginkgo": "Volt Ginkgo",  # Исправлено: было "Volt Gingko"
        "Cranberry": "Клюква",
        "Acorn": "Желудь",
        "Gingerbread": "Пряничный человечек",
        "Candycane": "Конфетная трость",
        "Cherry": "Вишня"
    }
    
    # Эмодзи для фруктов (русская версия)
    FRUIT_EMOJIS_RU = {
        "Груша": "🍐",
        "Ананас": "🍍",
        "Манго": "🥭",
        "Драконий фрукт": "🐲",
        "Bloodstone Cycad": "🩸",
        "Colossal Pinecone": "❇️",
        "Франкен Киви": "🥝",
        "Тыква": "🎃",
        "Дуриан": "❄️",
        "Конфета": "🍬",
        "Ракушка": "🐚",
        "Volt Ginkgo": "⚡️🦕",
        "Клюква": "🍇",
        "Желудь": "🌰",
        "Пряничный человечек": "🍪",
        "Конфетная трость": "🎄🍭",
        "Вишня": "🍒"
    }
    
    # Эмодзи для фруктов (английская версия - используем русские эмодзи)
    FRUIT_EMOJIS_EN = {
        "Pear": "🍐",
        "Pineapple": "🍍",
        "Gold Mango": "🥭",
        "Dragon Fruit": "🐲",
        "Bloodstone Cycad": "🩸",
        "Colossal Pinecone": "❇️",
        "Franken Kiwi": "🥝",
        "Pumpkin": "🎃",
        "Durian": "❄️",
        "Candy Corn": "🍬",
        "Deepsea Pearl Fruit": "🐚",
        "Volt Ginkgo": "⚡️🦕",
        "Cranberry": "🍇",
        "Acorn": "🌰",
        "Gingerbread": "🍪",
        "Candycane": "🎄🍭",
        "Cherry": "🍒"
    }
    
    # Фрукты, которые нужно выделять жирным (True/False)
    BOLD_FRUITS = {
        "Pear": False,
        "Pineapple": False,
        "Gold Mango": False,
        "Dragon Fruit": False,
        "Bloodstone Cycad": False,
        "Colossal Pinecone": False,
        "Franken Kiwi": True,
        "Pumpkin": True,
        "Durian": True,
        "Candy Corn": True,
        "Deepsea Pearl Fruit": True,
        "Volt Ginkgo": True,
        "Cranberry": True,
        "Acorn": True,
        "Gingerbread": True,
        "Candycane": True,
        "Cherry": True
    }
    
    # Словарь для замены @-версий фруктов
    REPLACE_WORDS = {
        "@Pear": "Pear",
        "@Pineapple": "Pineapple",
        "@Gold Mango": "Gold Mango",
        "@DragonFruit": "Dragon Fruit",
        "@BloodstoneCycad": "Bloodstone Cycad",
        "@ColossalPinecone": "Colossal Pinecone",
        "@FrankenKiwi": "Franken Kiwi",
        "@Pumpkin": "Pumpkin",
        "@Durian": "Durian",
        "@CandyCorn": "Candy Corn",
        "@DeepseaPearlFruit": "Deepsea Pearl Fruit",
        "@VoltGinkgo": "Volt Ginkgo",
        "@Cranberry": "Cranberry",
        "@Acorn": "Acorn",
        "@Gingerbread": "Gingerbread",
        "@Candycane": "Candycane",
        "@Cherry": "Cherry"
    }
    
    # Интервал проверки подписок (в секундах)
    SUBSCRIPTION_CHECK_INTERVAL = 21600  # 6 часов
    
    # Группа для публикации (если не задана отдельно — совпадает с REQUIRED_GROUP_ID)
    _publish_raw = os.getenv("PUBLISH_GROUP_ID", "").strip()
    PUBLISH_GROUP_ID = int(_publish_raw) if _publish_raw else int(os.getenv("REQUIRED_GROUP_ID", -1002927295087))
    
    # Включить/выключить функции
    GROUP_COMMANDS_ENABLED = True  # Команды для группы (калькулятор мутаций)
    ADMIN_PUBLISH_ENABLED = True   # Публикация админами в группу

    BACKUP_ENABLED = True
    AUTO_BACKUP_INTERVAL = 6  # Часы между автоматическими бэкапами
    MAX_BACKUP_FILES = 5     # Максимальное количество хранимых бэкапов
    BACKUP_COMPRESSION = True # Сжимать ли бэкапы

    # ID группы для обменов (с поддержкой тем/топиков)
    # Бот должен быть администратором группы с правом управления темами
    # None если переменная не задана — бот будет логировать ошибку при попытке создать топик
    _trade_group_raw = os.getenv("TRADE_ADMIN_GROUP_ID", "").strip()
    TRADE_ADMIN_GROUP_ID = int(_trade_group_raw) if _trade_group_raw else None

    # ID группы для чатов администратора с пользователями (с поддержкой тем/топиков).
    # Если не задана — используется TRADE_ADMIN_GROUP_ID (та же группа).
    # None = функция чата через группу недоступна.
    _chat_group_raw = os.getenv("CHAT_ADMIN_GROUP_ID", "").strip()
    if _chat_group_raw:
        CHAT_ADMIN_GROUP_ID = int(_chat_group_raw)
    elif _trade_group_raw:
        CHAT_ADMIN_GROUP_ID = int(_trade_group_raw)
    else:
        CHAT_ADMIN_GROUP_ID = None

    # ID группы для логов (инвентарь, обмены, розыгрыши, рассылки и т.д.)
    # 0 = логирование отключено
    LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", 0)) if os.getenv("LOG_GROUP_ID", "").strip() else 0

    # ID групп-администраторов: если написать !инв анонимно от имени группы — работает как команда от админа.
    # По умолчанию берётся REQUIRED_GROUP_ID. Можно переопределить через ADMIN_GROUP_IDS в .env.
    _admin_groups_raw = os.getenv("ADMIN_GROUP_IDS", "").strip()
    if _admin_groups_raw:
        ADMIN_GROUP_IDS: list[int] = [
            int(x.strip()) for x in _admin_groups_raw.split(",") if x.strip()
        ]
    else:
        ADMIN_GROUP_IDS: list[int] = [int(os.getenv("REQUIRED_GROUP_ID", -1002927295087))]
