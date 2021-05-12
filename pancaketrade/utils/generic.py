"""Generic utilities."""
import functools
import logging
from typing import Callable

from loguru import logger
from telegram import Update
from telegram.ext import CallbackContext


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def check_chat_id(func: Callable) -> Callable:
    """Compare chat ID with admin's chat ID and refuse access if unauthorized."""

    @functools.wraps(func)
    def wrapper_check_chat_id(tradebot, update: Update, context: CallbackContext, *args, **kwargs):
        if update.effective_chat is None:
            logger.debug('No chat ID')
            return
        if update.message is None:
            logger.debug('No message')
            return
        chat_id = update.effective_chat.id
        if chat_id == tradebot.config.secrets.admin_chat_id:
            return func(tradebot, update, context, *args, **kwargs)
        logger.warning(f'Prevented user {chat_id} to interact.')
        context.bot.send_message(
            chat_id=tradebot.config.secrets.admin_chat_id, text=f'Prevented user {chat_id} to interact.'
        )
        update.message.reply_text('This bot is not public, you are not allowed to use it.')

    return wrapper_check_chat_id
