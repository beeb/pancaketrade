from typing import NamedTuple

from loguru import logger
from pancaketrade.persistence import Token, db
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import check_chat_id
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
)
from web3 import Web3
from web3.types import ChecksumAddress
from peewee import IntegrityError


class AddTokenResponses(NamedTuple):
    ADDRESS: int = 0
    EMOJI: int = 1
    SLIPPAGE: int = 2


class AddTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.config = config
        self.next = AddTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CommandHandler('addtoken', self.command_addtoken)],
            states={
                self.next.ADDRESS: [MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_address)],
                self.next.EMOJI: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_emoji),
                    CallbackQueryHandler(self.command_addtoken_noemoji, pattern='^None$'),
                ],
                self.next.SLIPPAGE: [MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_slippage)],
            },
            fallbacks=[CommandHandler('canceltoken', self.command_canceltoken)],
            name='addtoken_conversation',
            persistent=True,
            conversation_timeout=120,
        )

    @check_chat_id
    def command_addtoken(self, update: Update, context: CallbackContext):
        assert update.message and context.user_data is not None
        context.user_data.clear()
        update.message.reply_html('Please send me the token contract address.')
        return self.next.ADDRESS

    @check_chat_id
    def command_addtoken_address(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        response = update.message.text.strip()
        if Web3.isAddress(response):
            token_address = Web3.toChecksumAddress(response)
        else:
            update.message.reply_html('The address you provided is not a valid ETH address. Try again:')
            return self.next.ADDRESS
        context.user_data['address'] = str(token_address)
        context.user_data['decimals'] = self.parent.net.get_token_decimals(token_address)
        context.user_data['symbol'] = self.parent.net.get_token_symbol(token_address)
        if self.token_exists(token_address):
            update.message.reply_html(f'Token <b>{context.user_data["symbol"]}</b> already exists.')
            context.user_data.clear()
            return ConversationHandler.END
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton('🙅‍♂️ No emoji', callback_data='None')]])
        update.message.reply_html(
            f'Thanks, the token <b>{context.user_data["symbol"]}</b> uses {context.user_data["decimals"]} decimals. '
            + 'Now please send me and EMOJI you would like to associate to this token for easy spotting, '
            + 'or click the button below.',
            reply_markup=reply_markup,
        )
        return self.next.EMOJI

    @check_chat_id
    def command_addtoken_emoji(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and update.effective_chat and context.user_data is not None
        context.user_data['icon'] = update.message.text.strip()
        update.message.reply_html(
            f'Alright, the token will show as <b>"{context.user_data["icon"]} {context.user_data["symbol"]}"</b>. '
            + 'What is the default slippage in % to use for swapping on PancakeSwap?'
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_addtoken_noemoji(self, update: Update, context: CallbackContext):
        assert context.user_data is not None and update.callback_query
        query = update.callback_query
        query.answer()
        context.user_data['icon'] = None
        query.edit_message_text(
            f'Alright, the token will show as <b>"{context.user_data["symbol"]}"</b>. '
            + 'What is the default slippage in % to use for swapping on PancakeSwap?'
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_addtoken_slippage(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and update.effective_chat and context.user_data is not None
        try:
            slippage = int(update.message.text.strip())
        except ValueError:
            update.message.reply_html(
                'This is not a valid slippage value. Please enter an integer number for percentage. Try again:'
            )
            return self.next.SLIPPAGE
        if slippage < 1:
            update.message.reply_html(
                'This is not a valid slippage value. Please enter a positive integer number for percentage. Try again:'
            )
            return self.next.SLIPPAGE
        context.user_data['default_slippage'] = slippage
        emoji = context.user_data['icon'] + ' ' if context.user_data['icon'] else ''
        update.message.reply_html(
            f'Alright, the token <b>{emoji}{context.user_data["symbol"]}</b> '
            + f'will use <b>{context.user_data["default_slippage"]}%</b> slippage by default.'
        )
        try:
            db.connect()
            with db.atomic():
                token = Token.create(**context.user_data)
        except IntegrityError:
            update.message.reply_html('Failed to create database record.')
            context.user_data.clear()
            return ConversationHandler.END
        finally:
            db.close()
        logger.info(token)
        context.user_data.clear()
        return ConversationHandler.END

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert update.message and context.user_data is not None
        context.user_data.clear()
        update.message.reply_html('OK, I\'m cancelling this command.')
        return ConversationHandler.END

    def token_exists(self, address: ChecksumAddress) -> bool:
        with db:
            count = Token.select().where(Token.address == str(address)).count()
        return count > 0
