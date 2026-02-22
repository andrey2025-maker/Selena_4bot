import re
from typing import Dict, List, Tuple, Optional
from config import Config

class MessageFilter:
    @staticmethod
    def clean_fruit_name(fruit_name: str) -> str:
        """Очистка названия фрукта от @ и замена по словарю"""
        # Убираем начальный @ если есть
        if fruit_name.startswith("@"):
            fruit_name = fruit_name[1:]
        
        # Заменяем по словарю REPLACE_WORDS
        for old, new in Config.REPLACE_WORDS.items():
            if old in fruit_name:
                fruit_name = fruit_name.replace(old, new)
                break
        
        # Дополнительные замены
        fruit_name = fruit_name.replace("DragonFruit", "Dragon Fruit")
        fruit_name = fruit_name.replace("BloodstoneCycad", "Bloodstone Cycad")
        fruit_name = fruit_name.replace("ColossalPinecone", "Colossal Pinecone")
        fruit_name = fruit_name.replace("FrankenKiwi", "Franken Kiwi")
        fruit_name = fruit_name.replace("DeepseaPearlFruit", "Deepsea Pearl Fruit")
        fruit_name = fruit_name.replace("VoltGinkgo", "Volt Ginkgo")
        fruit_name = fruit_name.replace("CandyCorn", "Candy Corn")
        fruit_name = fruit_name.replace("Candycane", "Candycane")
        
        return fruit_name.strip()
    
    @staticmethod
    def extract_fruits(text: str) -> List[Dict]:
        """
        Извлечение фруктов из сообщения о еде
        Формат: 〔🍇〕stock: FoodStock Update\nx1 @Pear
        """
        fruits = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            # Ищем паттерн типа x1 @Pear или x2 @Acorn
            match = re.match(r'x(\d+)\s+(.+)', line)
            if match:
                quantity = int(match.group(1))
                raw_fruit_name = match.group(2).strip()
                
                # Очищаем название фрукта
                fruit_name = MessageFilter.clean_fruit_name(raw_fruit_name)
                
                # Проверяем, является ли это известным фруктом
                if fruit_name in Config.AVAILABLE_FRUITS_EN:
                    fruits.append({
                        "name": fruit_name,
                        "quantity": quantity,
                        "raw_name": raw_fruit_name
                    })
        
        return fruits
    
    @staticmethod
    def get_fruit_emoji(fruit_name: str, lang: str = "EN") -> str:
        """Получение эмодзи для фрукта"""
        if lang in ("RUS", "ru"):
            from utils.messages import locale_manager
            russian_name = locale_manager.translate_fruit(fruit_name, lang)
            return Config.FRUIT_EMOJIS_RU.get(russian_name, "🍎")
        else:
            return Config.FRUIT_EMOJIS_EN.get(fruit_name, "🍎")
    
    @staticmethod
    def should_bold(fruit_name: str) -> bool:
        """Нужно ли выделять фрукт жирным"""
        return Config.BOLD_FRUITS.get(fruit_name, False)
    
    @staticmethod
    def format_food_message(fruits: List[Dict], lang: str = "EN") -> str:
        """Форматирование сообщения о еде для отправки - БЕЗ заголовка"""
        from utils.messages import locale_manager
        
        lines = []
        
        for fruit in fruits:
            fruit_name = fruit["name"]
            quantity = fruit["quantity"]
            
            # Получаем эмодзи для фрукта
            emoji = MessageFilter.get_fruit_emoji(fruit_name, lang)
            
            # Получаем отображаемое имя (с переводом для русского)
            if lang in ("RUS", "ru"):
                fruit_display = locale_manager.translate_fruit(fruit_name, "RUS")
            else:
                fruit_display = fruit_name
            
            # Проверяем, нужно ли выделять жирным
            if MessageFilter.should_bold(fruit_name):
                line = f"<b>{emoji} x{quantity} {fruit_display}</b> — stock"
            else:
                line = f"{emoji} x{quantity} {fruit_display} — stock"
            
            lines.append(line)
        
        # Возвращаем только список фруктов, БЕЗ заголовка
        return "\n".join(lines)
    
    @staticmethod
    def extract_totem_info(text: str) -> Tuple[Optional[str], str, Optional[str]]:
        """Извлечение информации о тотеме - ТОЛЬКО если есть ссылка Roblox"""
        # Определяем тип тотема
        is_free = "totem-free:" in text.lower()
        is_paid = "totem-paid:" in text.lower()
        
        if not (is_free or is_paid):
            return None, text, None
        
        totem_type = "free" if is_free else "paid"
        
        # Удаляем префикс тотема
        cleaned_text = text.replace(f"totem-{totem_type}:", "").strip()
        
        # Ищем ссылку Roblox - ОБЯЗАТЕЛЬНО должна быть!
        link_pattern = r'(https://www\.roblox\.com/[^\s]+Server)'
        match = re.search(link_pattern, cleaned_text)
        
        # ЕСЛИ ССЫЛКИ НЕТ - не отправляем тотем
        if not match:
            return None, text, None
        
        link = match.group(1)
        
        # Удаляем ссылку из текста для чистого сообщения
        if link:
            cleaned_text = cleaned_text.replace(link, "").strip()
        
        return totem_type, cleaned_text, link
    
    @staticmethod
    def format_totem_message(totem_type: str, text: str, link: Optional[str], lang: str = "EN") -> str:
        """Форматирование сообщения о тотеме с кликабельной ссылкой в заголовке (HTML)."""
        import re
        import html

        # Определяем базовое название (поддерживаем оба формата: "RUS"/"EN" и "ru"/"en")
        if lang in ("RUS", "ru"):
            if totem_type == "free":
                title_emoji = "🗿"
                title_base = "Бесплатный тотем"
            else:
                title_emoji = "💎"
                title_base = "Платный тотем"
        else:
            if totem_type == "free":
                title_emoji = "🗿"
                title_base = "Free totem"
            else:
                title_emoji = "💎"
                title_base = "Paid totem"

        # Формируем заголовок в HTML (единый parse_mode для всего бота)
        if link:
            safe_link = html.escape(link, quote=True)
            title = f'{title_emoji} <a href="{safe_link}"><b>{title_base}</b></a>:'
            # Убираем ссылку из текста если она там есть
            text = text.replace(f"({link})", "").replace(link, "").strip()
        else:
            title = f"{title_emoji} <b>{title_base}</b>:"

        # Экранируем HTML-спецсимволы в тексте тотема и очищаем лишние пробелы
        text = html.escape(re.sub(r'\s+', ' ', text).strip())

        return f"{title}\n\n{text}"
    
    @staticmethod
    def classify_message(text: str) -> Dict:
        """Классификация входящего сообщения"""
        text_lower = text.lower()
        
        if "stock:" in text_lower and "foodstock update" in text_lower:
            fruits = MessageFilter.extract_fruits(text)
            if fruits:
                return {
                    "type": "food",
                    "data": fruits
                }
        
        totem_type, cleaned_text, link = MessageFilter.extract_totem_info(text)
        if totem_type:
            return {
                "type": "totem",
                "subtype": totem_type,
                "text": cleaned_text,
                "link": link
            }
        
        return {"type": "unknown"}