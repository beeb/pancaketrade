"""Bot class."""
import functools
from typing import Callable
from loguru import logger

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler, PicklePersistence, Updater

from pancaketrade.network.bsc import Network
from pancaketrade.persistence.models import db, init_db
from pancaketrade.utils.config import Config


def check_chat_id(func: Callable) -> Callable:
    """Compare chat ID with admin's chat ID and refuse access if unauthorized."""

    @functools.wraps(func)
    def wrapper_check_chat_id(*args, **kwargs):
        print(args)
        print(kwargs)
        return func(*args, **kwargs)

    return wrapper_check_chat_id


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.net = Network(secrets=self.config.secrets)
        self.db = db
        init_db()
        persistence = PicklePersistence(filename='botpersistence')
        self.updater = Updater(token=config.secrets.telegram_token, persistence=persistence)
        self.dispatcher = self.updater.dispatcher
        self.setup_telegram()

    def setup_telegram(self):
        self.dispatcher.add_handler(CommandHandler('start', self.command_start))

    def start(self):
        self.dispatcher.bot.send_message(chat_id=self.config.secrets.admin_chat_id, text='Bot started')
        logger.info('Bot started')
        self.updater.start_polling()
        self.updater.idle()

    @check_chat_id
    def command_start(self, message: Update, context: CallbackContext):
        pass
