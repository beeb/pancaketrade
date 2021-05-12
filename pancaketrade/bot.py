"""Bot class."""
import functools
from typing import Callable, NamedTuple

from loguru import logger
from telegram import Update
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    PicklePersistence,
    Updater,
    ConversationHandler,
    MessageHandler,
    Filters,
)

from pancaketrade.network.bsc import Network
from pancaketrade.persistence.models import db, init_db
from pancaketrade.utils.config import Config


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


class ChatResponses(NamedTuple):
    ADD_TOKEN_ADDRESS: int = 0


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.net = Network(wallet=self.config.wallet, secrets=self.config.secrets)
        self.db = db
        init_db()
        persistence = PicklePersistence(filename='botpersistence')
        self.updater = Updater(token=config.secrets.telegram_token, persistence=persistence)
        self.dispatcher = self.updater.dispatcher
        self.next = ChatResponses()
        self.setup_telegram()

    def setup_telegram(self):
        self.dispatcher.add_handler(CommandHandler('start', self.command_start))
        self.dispatcher.add_handler(CommandHandler('status', self.command_status))
        addtoken_handler = ConversationHandler(
            entry_points=[CommandHandler('addtoken', self.command_addtoken)],
            states={
                self.next.ADD_TOKEN_ADDRESS: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_address)
                ]
            },
            fallbacks=[CommandHandler('canceltoken', self.command_canceltoken)],
            name='addtoken_conversation',
            persistent=True,
        )
        self.dispatcher.add_handler(addtoken_handler)

    def start(self):
        self.dispatcher.bot.send_message(chat_id=self.config.secrets.admin_chat_id, text='Bot started')
        logger.info('Bot started')
        self.updater.start_polling()
        self.updater.idle()

    @check_chat_id
    def command_start(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html(
            'Hi! You can start adding tokens that you want to trade with the <a href="/addtoken">/addtoken</a> command.'
        )

    @check_chat_id
    def command_status(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        balance_bnb = self.net.get_bnb_balance()
        update.message.reply_html(f'BNB in wallet: {balance_bnb:.4f}')

    @check_chat_id
    def command_addtoken(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html('Please send me the token contract address.')
        return self.next.ADD_TOKEN_ADDRESS

    @check_chat_id
    def command_addtoken_address(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html('OK, I\'m cancelling this command.')
        return ConversationHandler.END
