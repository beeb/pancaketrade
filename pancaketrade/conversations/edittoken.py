from typing import NamedTuple

from pancaketrade.network import Network
from pancaketrade.persistence import db
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import check_chat_id, chat_message
from pancaketrade.watchers import TokenWatcher
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


class EditTokenResponses(NamedTuple):
    EMOJI: int = 1
    SLIPPAGE: int = 2


class EditTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = EditTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_edittoken, pattern='^edittoken:0x[a-fA-F0-9]{40}$')],
            states={
                self.next.EMOJI: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_edittoken_emoji),
                    CallbackQueryHandler(self.command_edittoken_emoji, pattern='^[^:]*$'),
                ],
                self.next.SLIPPAGE: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_edittoken_slippage),
                    CallbackQueryHandler(self.command_edittoken_slippage, pattern='^[^:]*$'),
                ],
            },
            fallbacks=[CommandHandler('cancel', self.command_canceltoken)],
            name='edittoken_conversation',
        )

    @check_chat_id
    def command_edittoken(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        assert query.data
        token_address = query.data.split(':')[1]
        if not Web3.isChecksumAddress(token_address):
            self.command_error(update, context, text='Invalid token address.')
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[token_address]
        context.user_data['edittoken'] = {'token_address': token_address}
        buttons = [InlineKeyboardButton('🙅‍♂️ No emoji', callback_data='None')]
        if token.emoji:
            buttons.insert(0, InlineKeyboardButton(f'Keep {token.emoji}', callback_data=token.emoji))
        reply_markup = InlineKeyboardMarkup([buttons])
        chat_message(
            update,
            context,
            text=f'Please send me and EMOJI you would like to associate with {token.symbol} for easy spotting, '
            + 'or click the buttons below.',
            reply_markup=reply_markup,
            edit=False,
        )
        return self.next.EMOJI

    @check_chat_id
    def command_edittoken_emoji(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data['edittoken']
        token: TokenWatcher = self.parent.watchers[edit['token_address']]
        if update.message is not None:
            assert update.message.text
            edit['icon'] = update.message.text.strip()
        else:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == 'None':
                edit['icon'] = None
            else:
                edit['icon'] = query.data
        emoji = edit['icon'] + ' ' if edit['icon'] else ''
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f'{token.default_slippage}%', callback_data=token.default_slippage)]]
        )
        chat_message(
            update,
            context,
            text='Alright, the token will show as '
            + f'<b>"{emoji}{token.symbol}"</b>. '
            + 'What is the default slippage in % to use for swapping on PancakeSwap?',
            edit=False,
            reply_markup=reply_markup,
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_edittoken_slippage(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data['edittoken']
        token: TokenWatcher = self.parent.watchers[edit['token_address']]
        if update.message is not None:
            assert update.message.text
            try:
                slippage = int(update.message.text.strip())
            except ValueError:
                chat_message(
                    update,
                    context,
                    text='⚠️ This is not a valid slippage value. Please enter an integer number for percentage '
                    + '(without percent sign). Try again:',
                    edit=False,
                )
                return self.next.SLIPPAGE
        else:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            try:
                slippage = int(query.data)
            except ValueError:
                self.command_error(update, context, text='Invalid default slippage.')
                return ConversationHandler.END
        if slippage < 1:
            chat_message(
                update,
                context,
                text='⚠️ This is not a valid slippage value. Please enter a positive integer number for percentage. '
                + 'Try again:',
                edit=False,
            )
            return self.next.SLIPPAGE
        edit['default_slippage'] = slippage
        emoji = edit['icon'] + ' ' if edit['icon'] else ''
        chat_message(
            update,
            context,
            text=f'Alright, the token <b>{emoji}{token.symbol}</b> '
            + f'will use <b>{edit["default_slippage"]}%</b> slippage by default.',
            edit=False,
        )
        token_record = token.token_record
        try:
            db.connect()
            with db.atomic():
                token_record.icon = edit['icon']
                token_record.default_slippage = edit['default_slippage']
                token_record.save()
        except Exception as e:
            chat_message(update, context, text=f'⛔ Failed to update database record: {e}', edit=False)
            del context.user_data['edittoken']
            return ConversationHandler.END
        finally:
            del context.user_data['edittoken']
            db.close()
        token.emoji = token_record.icon + ' ' if token_record.icon else ''
        token.name = token.emoji + token.symbol
        token.default_slippage = token_record.default_slippage
        chat_message(
            update,
            context,
            text='✅ Token was edited successfully.',
            edit=False,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        del context.user_data['edittoken']
        chat_message(update, context, text='⚠️ OK, I\'m cancelling this command.', edit=False)
        return ConversationHandler.END

    def command_error(self, update: Update, context: CallbackContext, text: str):
        assert context.user_data is not None
        del context.user_data['edittoken']
        chat_message(update, context, text=f'⛔️ {text}', edit=False)
