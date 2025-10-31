import logging
from logging.handlers import RotatingFileHandler
import sys

def setup_logging():
    """Настраивает подробное логирование"""
    
    # Уровень логирования (DEBUG — самый подробный)
    LOGGING_LEVEL = logging.DEBUG
    
    # Форматтер
    # asctime - время, levelname - уровень (INFO, ERROR), name - имя логгера, lineno - номер строки, message - само сообщение.
    log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    root_logger = logging.getLogger()
    root_logger.setLevel(LOGGING_LEVEL)
    
    # RotatingFileHandler автоматически управляет размером файла логов.
    # Когда достигается maxBytes, он переименовывается, и создается новый.
    # backupCount — сколько старых файлов хранить.
    file_handler = RotatingFileHandler('bot.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
    
    # Вывод логов в консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    # В консоли показываем только INFO и выше
    console_handler.setLevel(logging.INFO) 
    root_logger.addHandler(console_handler)
    
    logging.info("Система логирования успешно настроена.")