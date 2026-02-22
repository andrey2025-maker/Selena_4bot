"""
backup_utils.py - Утилиты для создания и управления бэкапами базы данных
"""

import sqlite3
import os
import shutil
import gzip
import json
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, List
from config import Config

logger = logging.getLogger(__name__)

class DatabaseBackup:
    def __init__(self, db_path: str = Config.DATABASE_PATH):
        self.db_path = db_path
        self.backup_dir = "database_backups"
        self.max_backups = 30  # Хранить максимум 30 бэкапов
        self.ensure_backup_dir()
    
    def ensure_backup_dir(self):
        """Создание папки для бэкапов"""
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)
            logger.info(f"Создана папка для бэкапов: {self.backup_dir}")
    
    def create_backup(self, compress: bool = True) -> Optional[str]:
        """
        Создание бэкапа базы данных
        
        Args:
            compress: Сжимать ли файл с помощью gzip
            
        Returns:
            Путь к созданному бэкапу или None в случае ошибки
        """
        if not os.path.exists(self.db_path):
            logger.error(f"База данных не найдена: {self.db_path}")
            return None
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if compress:
                backup_name = f"database_backup_{timestamp}.db.gz"
                backup_path = os.path.join(self.backup_dir, backup_name)
                
                # Создаем сжатый бэкап
                with open(self.db_path, 'rb') as f_in:
                    with gzip.open(backup_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                
                # Получаем размеры файлов
                original_size = os.path.getsize(self.db_path)
                compressed_size = os.path.getsize(backup_path)
                compression_ratio = (1 - compressed_size / original_size) * 100
                
                logger.info(f"Создан сжатый бэкап: {backup_name}")
                logger.info(f"Размер: {original_size:,} → {compressed_size:,} байт ({compression_ratio:.1f}% сжатия)")
                
            else:
                backup_name = f"database_backup_{timestamp}.db"
                backup_path = os.path.join(self.backup_dir, backup_name)
                
                # Просто копируем файл
                shutil.copy2(self.db_path, backup_path)
                
                file_size = os.path.getsize(backup_path)
                logger.info(f"Создан бэкап: {backup_name} ({file_size:,} байт)")
            
            # Очищаем старые бэкапы
            self.cleanup_old_backups()
            
            return backup_path
            
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")
            return None
    
    def create_json_backup(self) -> Optional[str]:
        """Создание бэкапа в формате JSON (легче для чтения)"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Получаем все таблицы
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            backup_data = {
                "timestamp": datetime.now().isoformat(),
                "tables": {}
            }
            
            for table in tables:
                cursor.execute(f"SELECT * FROM {table}")
                rows = cursor.fetchall()
                
                # Конвертируем строки в словари
                table_data = []
                for row in rows:
                    table_data.append(dict(row))
                
                backup_data["tables"][table] = table_data
            
            conn.close()
            
            # Сохраняем в JSON
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_name = f"database_backup_{timestamp}.json"
            json_path = os.path.join(self.backup_dir, json_name)
            
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)
            
            file_size = os.path.getsize(json_path)
            logger.info(f"Создан JSON бэкап: {json_name} ({file_size:,} байт)")
            
            return json_path
            
        except Exception as e:
            logger.error(f"Ошибка создания JSON бэкапа: {e}")
            return None
    
    def cleanup_old_backups(self):
        """Удаление старых бэкапов"""
        try:
            backup_files = []
            for filename in os.listdir(self.backup_dir):
                if filename.startswith("database_backup_"):
                    filepath = os.path.join(self.backup_dir, filename)
                    backup_files.append((filepath, os.path.getmtime(filepath)))
            
            # Сортируем по дате изменения (старые первыми)
            backup_files.sort(key=lambda x: x[1])
            
            # Удаляем лишние файлы
            while len(backup_files) > self.max_backups:
                old_file = backup_files.pop(0)[0]
                os.remove(old_file)
                logger.info(f"Удален старый бэкап: {os.path.basename(old_file)}")
                
        except Exception as e:
            logger.error(f"Ошибка очистки бэкапов: {e}")
    
    def get_backup_stats(self) -> Dict:
        """Получение статистики по бэкапам"""
        stats = {
            "total_backups": 0,
            "total_size": 0,
            "oldest_backup": None,
            "newest_backup": None,
            "backup_types": {"db": 0, "db.gz": 0, "json": 0}
        }
        
        try:
            for filename in os.listdir(self.backup_dir):
                if filename.startswith("database_backup_"):
                    filepath = os.path.join(self.backup_dir, filename)
                    stats["total_backups"] += 1
                    stats["total_size"] += os.path.getsize(filepath)
                    
                    # Определяем тип файла
                    if filename.endswith('.db.gz'):
                        stats["backup_types"]["db.gz"] += 1
                    elif filename.endswith('.db'):
                        stats["backup_types"]["db"] += 1
                    elif filename.endswith('.json'):
                        stats["backup_types"]["json"] += 1
                    
                    # Обновляем даты
                    mtime = os.path.getmtime(filepath)
                    mtime_dt = datetime.fromtimestamp(mtime)
                    
                    if not stats["oldest_backup"] or mtime_dt < stats["oldest_backup"]:
                        stats["oldest_backup"] = mtime_dt
                    
                    if not stats["newest_backup"] or mtime_dt > stats["newest_backup"]:
                        stats["newest_backup"] = mtime_dt
            
            # Форматируем размер
            if stats["total_size"] > 0:
                if stats["total_size"] > 1024 * 1024:  # MB
                    stats["total_size_formatted"] = f"{stats['total_size'] / (1024 * 1024):.2f} MB"
                elif stats["total_size"] > 1024:  # KB
                    stats["total_size_formatted"] = f"{stats['total_size'] / 1024:.2f} KB"
                else:
                    stats["total_size_formatted"] = f"{stats['total_size']} байт"
            else:
                stats["total_size_formatted"] = "0 байт"
                
        except Exception as e:
            logger.error(f"Ошибка получения статистики бэкапов: {e}")
        
        return stats
    
    def list_backups(self) -> List[Dict]:
        """Список всех бэкапов"""
        backups = []
        
        try:
            for filename in os.listdir(self.backup_dir):
                if filename.startswith("database_backup_"):
                    filepath = os.path.join(self.backup_dir, filename)
                    
                    backup_info = {
                        "filename": filename,
                        "path": filepath,
                        "size": os.path.getsize(filepath),
                        "modified": datetime.fromtimestamp(os.path.getmtime(filepath)),
                        "type": "unknown"
                    }
                    
                    if filename.endswith('.db.gz'):
                        backup_info["type"] = "compressed"
                    elif filename.endswith('.db'):
                        backup_info["type"] = "database"
                    elif filename.endswith('.json'):
                        backup_info["type"] = "json"
                    
                    # Форматируем размер
                    size = backup_info["size"]
                    if size > 1024 * 1024:
                        backup_info["size_formatted"] = f"{size / (1024 * 1024):.2f} MB"
                    elif size > 1024:
                        backup_info["size_formatted"] = f"{size / 1024:.2f} KB"
                    else:
                        backup_info["size_formatted"] = f"{size} байт"
                    
                    backups.append(backup_info)
            
            # Сортируем по дате (новые первыми)
            backups.sort(key=lambda x: x["modified"], reverse=True)
            
        except Exception as e:
            logger.error(f"Ошибка получения списка бэкапов: {e}")
        
        return backups

# Глобальный экземпляр
backup_manager = DatabaseBackup()
