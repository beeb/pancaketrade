from decimal import Decimal
from typing import NamedTuple

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
from web3.exceptions import ABIFunctionNotFound, ContractLogicError

from pancaketrade.network import Network
from pancaketrade.persistence import Token, db
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import token_exists
from pancaketrade.utils.generic import chat_message, check_chat_id, format_token_amount
from pancaketrade.watchers import TokenWatcher


class AddTokenResponses(NamedTuple):
    ADDRESS: int = 0
    EMOJI: int = 1
    SLIPPAGE: int = 2


class AddTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = AddTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CommandHandler("addtoken", self.command_addtoken)],
            states={
                self.next.ADDRESS: [MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_address)],
                self.next.EMOJI: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_emoji),
                    CallbackQueryHandler(self.command_addtoken_noemoji, pattern="^None$"),
                ],
                self.next.SLIPPAGE: [MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_slippage)],
            },
            fallbacks=[CommandHandler("cancel", self.command_canceltoken)],
            name="addtoken_conversation",
        )

    @check_chat_id
    def command_addtoken(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        context.user_data["addtoken"] = {}
        chat_message(update, context, text="Please send me the token contract address.", edit=False)
        return self.next.ADDRESS

    @check_chat_id
    def command_addtoken_address(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        response = update.message.text.strip()
        if Web3.isAddress(response):
            token_address = Web3.toChecksumAddress(response)
        else:
            chat_message(
                update, context, text="⚠️ The address you provided is not a valid ETH address. Try again:", edit=False
            )
            return self.next.ADDRESS
        add = context.user_data["addtoken"]
        add["address"] = str(token_address)
        try:
            add["decimals"] = self.net.get_token_decimals(token_address)
            add["symbol"] = self.net.get_token_symbol(token_address)
        except (ABIFunctionNotFound, ContractLogicError):
            chat_message(
                update,
                context,
                text="⛔ Wrong ABI for this address.\n"
                + "Check that address is a contract at "
                + f'<a href="https://bscscan.com/address/{token_address}">BscScan</a> and try again.',
                edit=False,
            )
            del context.user_data["addtoken"]
            return ConversationHandler.END

        if token_exists(address=token_address):
            chat_message(update, context, text=f'⚠️ Token <b>{add["symbol"]}</b> already exists.', edit=False)
            del context.user_data["addtoken"]
            return ConversationHandler.END
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🙅‍♂️ No emoji", callback_data="None")]])
        chat_message(
            update,
            context,
            text=f'Thanks, the token <b>{add["symbol"]}</b> uses '
            + f'{add["decimals"]} decimals. '
            + "Now please send me and EMOJI you would like to associate to this token for easy spotting, "
            + "or click the button below.",
            reply_markup=reply_markup,
            edit=False,
        )
        return self.next.EMOJI

    @check_chat_id
    def command_addtoken_emoji(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        add = context.user_data["addtoken"]
        add["icon"] = update.message.text.strip()
        chat_message(
            update,
            context,
            text="Alright, the token will show as "
            + f'<b>"{add["icon"]} {add["symbol"]}"</b>. '
            + "What is the default slippage in % to use for swapping on PancakeSwap?",
            edit=False,
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_addtoken_noemoji(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        add = context.user_data["addtoken"]
        add["icon"] = None
        chat_message(
            update,
            context,
            text=f'Alright, the token will show as <b>"{add["symbol"]}"</b>. '
            + "What is the default slippage in % to use for swapping on PancakeSwap?",
            edit=self.config.update_messages,
        )
        return self.next.SLIPPAGE

    @check_chat_id
    def command_addtoken_slippage(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and context.user_data is not None
        try:
            slippage = Decimal(update.message.text.strip())
        except Exception:
            chat_message(
                update,
                context,
                text="⚠️ This is not a valid slippage value. Please enter a decimal number for percentage. Try again:",
                edit=False,
            )
            return self.next.SLIPPAGE
        if slippage < Decimal("0.01") or slippage > 100:
            chat_message(
                update,
                context,
                text="⚠️ This is not a valid slippage value. Please enter a number between 0.01 and 100 for "
                + "percentage. Try again:",
                edit=False,
            )
            return self.next.SLIPPAGE
        add = context.user_data["addtoken"]
        add["default_slippage"] = f"{slippage:.2f}"
        emoji = add["icon"] + " " if add["icon"] else ""

        chat_message(
            update,
            context,
            text=f'Alright, the token <b>{emoji}{add["symbol"]}</b> '
            + f'will use <b>{add["default_slippage"]}%</b> slippage by default.',
            edit=False,
        )
        try:
            with db.atomic():
                token_record = Token.create(**add)
        except Exception as e:
            chat_message(update, context, text=f"⛔ Failed to create database record: {e}", edit=False)
            del context.user_data["addtoken"]
            return ConversationHandler.END
        finally:
            del context.user_data["addtoken"]
        token = TokenWatcher(token_record=token_record, net=self.net, dispatcher=context.dispatcher, config=self.config)
        self.parent.watchers[token.address] = token
        balance = self.net.get_token_balance(token_address=token.address)
        balance_usd = self.net.get_token_balance_usd(token_address=token.address, balance=balance)
        buttons = [
            [
                InlineKeyboardButton("➕ Create order", callback_data=f"addorder:{token.address}"),
                InlineKeyboardButton("💰 Buy/Sell now", callback_data=f"buysell:{token.address}"),
            ]
        ]
        if not self.net.is_approved(token_address=token.address):
            buttons.append([InlineKeyboardButton("☑️ Approve for selling", callback_data=f"approve:{token.address}")])
        reply_markup = InlineKeyboardMarkup(buttons)
        chat_message(
            update,
            context,
            text="✅ Token was added successfully. "
            + f"Balance is {format_token_amount(balance)} {token.symbol} (${balance_usd:.2f}).",
            reply_markup=reply_markup,
            edit=False,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        del context.user_data["addtoken"]
        chat_message(update, context, text="⚠️ OK, I'm cancelling this command.", edit=False)
        return ConversationHandler.END
