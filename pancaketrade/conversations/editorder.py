from decimal import Decimal
from typing import List, NamedTuple

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
from web3.types import Wei

from pancaketrade.network import Network
from pancaketrade.persistence.models import db
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import chat_message, check_chat_id, format_price_fixed, format_token_amount
from pancaketrade.watchers import OrderWatcher, TokenWatcher


class EditOrderResponses(NamedTuple):
    ORDER_CHOICE: int = 0
    ACTION_CHOICE: int = 1
    PRICE: int = 2
    TRAILING: int = 3
    AMOUNT: int = 4
    SLIPPAGE: int = 5
    GAS: int = 6


class EditOrderConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = EditOrderResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_editorder, pattern="^editorder:0x[a-fA-F0-9]{40}$")],
            states={
                self.next.ORDER_CHOICE: [CallbackQueryHandler(self.command_edittoken_orderchoice, pattern="^[^:]*$")],
                self.next.ACTION_CHOICE: [
                    CallbackQueryHandler(
                        self.command_editorder_action,
                        pattern="^price$|^trailing_stop$|^amount$|^slippage$|^gas$|^cancel$",
                    )
                ],
                self.next.PRICE: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_editorder_price),
                    CallbackQueryHandler(self.command_editorder_price, pattern="^[^:]*$"),
                ],
                self.next.TRAILING: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_editorder_tsl),
                    CallbackQueryHandler(self.command_editorder_tsl, pattern="^[^:]*$"),
                ],
                self.next.AMOUNT: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_editorder_amount),
                    CallbackQueryHandler(self.command_editorder_amount, pattern="^[^:]*$"),
                ],
                self.next.SLIPPAGE: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_editorder_slippage),
                    CallbackQueryHandler(self.command_editorder_slippage, pattern="^[^:]*$"),
                ],
                self.next.GAS: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_editorder_gas),
                    CallbackQueryHandler(self.command_editorder_gas, pattern="^[^:]*$"),
                ],
            },
            fallbacks=[CommandHandler("cancel", self.command_cancelorder)],
            name="editorder_conversation",
        )
        self.symbol_usd = "$" if self.config.price_in_usd else ""
        self.symbol_bnb = "BNB" if not self.config.price_in_usd else ""

    @check_chat_id
    def command_editorder(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        assert query.data
        token_address = query.data.split(":")[1]
        if not Web3.isChecksumAddress(token_address):
            self.command_error(update, context, text="Invalid token address.")
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[token_address]
        context.user_data["editorder"] = {"token_address": token_address}
        orders = token.orders
        orders_sorted = sorted(orders, key=lambda o: o.limit_price if o.limit_price else Decimal(1e12), reverse=True)
        orders_display = [str(order) for order in orders_sorted]
        buttons: List[InlineKeyboardButton] = [
            InlineKeyboardButton(
                f"{self.get_type_icon(o)} #{o.order_record.id} - {self.get_type_name(o)}",
                callback_data=o.order_record.id,
            )
            for o in orders
        ]
        buttons_layout = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        buttons_layout.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons_layout)
        chat_message(
            update,
            context,
            text=f"Select the order you want to edit for {token.name}.\n\n" + "\n".join(orders_display),
            reply_markup=reply_markup,
            edit=self.config.update_messages,
        )
        return self.next.ORDER_CHOICE

    @check_chat_id
    def command_edittoken_orderchoice(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        if query.data == "cancel":
            self.cancel_command(update, context)
            return ConversationHandler.END
        assert query.data
        if not query.data.isdecimal():
            self.command_error(update, context, text="Invalid order ID")
            return ConversationHandler.END
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == int(str(query.data)), token.orders))
        edit["order_id"] = int(str(query.data))
        chat_message(update, context, text=order.long_str(), edit=self.config.update_messages)
        buttons = [
            [
                InlineKeyboardButton("Edit price", callback_data="price"),
                InlineKeyboardButton("Edit tsl callback", callback_data="trailing_stop"),
            ],
            [
                InlineKeyboardButton("Edit amount", callback_data="amount"),
                InlineKeyboardButton("Edit slippage", callback_data="slippage"),
            ],
            [
                InlineKeyboardButton("Edit gas price", callback_data="gas"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        chat_message(
            update,
            context,
            text=f'What do you want to edit for order {edit["order_id"]}?',
            reply_markup=reply_markup,
            edit=False,
        )
        return self.next.ACTION_CHOICE

    @check_chat_id
    def command_editorder_action(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        if query.data == "cancel":
            self.cancel_command(update, context)
            return ConversationHandler.END
        assert query.data
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == edit["order_id"], token.orders))
        if query.data == "price":
            buttons = [
                [
                    InlineKeyboardButton("⏱ Execute now", callback_data="None"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            current_price, _ = self.net.get_token_price(token_address=token.address)
            current_price_fixed = format_price_fixed(current_price)
            chat_message(
                update,
                context,
                text=f"Please indicate the <u>price in <b>{self.symbol_usd}{self.symbol_bnb} per {token.symbol}"
                + "</b></u> at which the order will activate.\n"
                + "You have 4 options for this:\n"
                + f' ・ Standard notation like "<code>{current_price_fixed}</code>"\n'
                + f' ・ Scientific notation like "<code>{current_price:.1e}</code>"\n'
                + ' ・ Multiplier for the current price like "<code>1.5x</code>" (include the "x" at the end)\n'
                + " ・ Trigger now by clicking the button below (if trailing stop loss enabled then it will not "
                + "execute immediately).\n"
                + f"<b>Current price</b>: {self.symbol_usd}<code>{current_price:.4g}</code> {self.symbol_bnb} "
                + f"per {token.symbol}.",
                reply_markup=reply_markup,
                edit=self.config.update_messages,
            )
            return self.next.PRICE
        elif query.data == "trailing_stop":
            buttons = [
                [
                    InlineKeyboardButton("1%", callback_data="1"),
                    InlineKeyboardButton("2%", callback_data="2"),
                    InlineKeyboardButton("5%", callback_data="5"),
                    InlineKeyboardButton("10%", callback_data="10"),
                ],
                [
                    InlineKeyboardButton("No trailing stop loss", callback_data="None"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            chat_message(
                update,
                context,
                text="Do you want to enable <u>trailing stop loss</u>? If yes, what is the callback rate?\n",
                reply_markup=reply_markup,
                edit=self.config.update_messages,
            )
            return self.next.TRAILING
        elif query.data == "amount":
            unit = "BNB" if order.type == "buy" else token.symbol
            balance = (
                self.net.get_bnb_balance()
                if order.type == "buy"
                else self.net.get_token_balance(token_address=token.address)
            )
            reply_markup = (
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton("25%", callback_data="0.25"),
                            InlineKeyboardButton("50%", callback_data="0.5"),
                            InlineKeyboardButton("75%", callback_data="0.75"),
                            InlineKeyboardButton("100%", callback_data="1.0"),
                        ],
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
                    ]
                )
                if order.type == "sell"
                else InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
            )
            chat_message(
                update,
                context,
                text=f"<u>How much {unit}</u> do you want me to use for {order.type}ing?\n"
                + f"You can also use scientific notation like <code>{balance:.1e}</code> or a percentage like "
                + "<code>63%</code>.\n"
                + f"<b>Current balance</b>: <code>{format_token_amount(balance)}</code> {unit}",
                reply_markup=reply_markup,
                edit=False,
            )
            return self.next.AMOUNT
        elif query.data == "slippage":
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            f"{token.default_slippage}% (default)", callback_data=str(token.default_slippage)
                        ),
                        InlineKeyboardButton("0.5%", callback_data="0.5"),
                        InlineKeyboardButton("1%", callback_data="1"),
                        InlineKeyboardButton("2%", callback_data="2"),
                    ],
                    [
                        InlineKeyboardButton("5%", callback_data="5"),
                        InlineKeyboardButton("10%", callback_data="10"),
                        InlineKeyboardButton("15%", callback_data="15"),
                        InlineKeyboardButton("20%", callback_data="20"),
                    ],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
                ]
            )
            chat_message(
                update,
                context,
                text="Please indicate the <u>slippage in percent</u> you want to use for this order.\n"
                + "You can also message me a custom value in percent.",
                reply_markup=reply_markup,
                edit=self.config.update_messages,
            )
            return self.next.SLIPPAGE
        elif query.data == "gas":
            network_gas_price = Decimal(self.net.w3.eth.gas_price) / Decimal(10**9)
            chat_message(
                update,
                context,
                text="Please indicate the <u>gas price in Gwei</u> for this order.\n"
                + 'Choose "Default" to use the default network price at the moment '
                + f"of the transaction (currently {network_gas_price:.1f} Gwei) "
                + "or message me the value.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("network default", callback_data="None"),
                            InlineKeyboardButton("default + 0.1 Gwei", callback_data="+0.1"),
                        ],
                        [
                            InlineKeyboardButton("default + 1 Gwei", callback_data="+1"),
                            InlineKeyboardButton("default + 2 Gwei", callback_data="+2"),
                        ],
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
                    ]
                ),
                edit=self.config.update_messages,
            )
            return self.next.GAS
        else:
            self.command_error(update, context, text="Invalid callback")
            return ConversationHandler.END

    @check_chat_id
    def command_editorder_price(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == edit["order_id"], token.orders))
        if update.message is None:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == "cancel":
                self.cancel_command(update, context)
                return ConversationHandler.END
            elif query.data == "None":  # update order with None price = execute now
                edit["limit_price"] = ""  # empty string = market price
                order_record = order.order_record
                try:
                    with db.atomic():
                        order_record.limit_price = edit["limit_price"]
                        order_record.save()
                except Exception as e:
                    self.command_error(update, context, text=f"Failed to update database record: {e}")
                    return ConversationHandler.END
                finally:
                    del context.user_data["editorder"]
                order.limit_price = None
                chat_message(
                    update,
                    context,
                    text="✅ Alright, the order will execute soon at market price.",
                    edit=self.config.update_messages,
                )
                return ConversationHandler.END
            else:
                self.command_error(update, context, text="Invalid callback.")
                return ConversationHandler.END

        assert update.message and update.message.text
        answer = update.message.text.strip()
        if answer.endswith("x"):
            try:
                factor = Decimal(answer[:-1])
            except Exception:
                reply_markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("⏱ Execute now", callback_data="None"),
                            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                        ]
                    ]
                )
                chat_message(
                    update,
                    context,
                    text="⚠️ The factor you inserted is not valid. Try again:",
                    reply_markup=reply_markup,
                    edit=False,
                )
                return self.next.PRICE
            current_price, _ = self.net.get_token_price(token_address=token.address)
            price = factor * current_price
        else:
            try:
                price = Decimal(answer)
            except Exception:
                reply_markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("⏱ Execute now", callback_data="None"),
                            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                        ]
                    ]
                )
                chat_message(
                    update,
                    context,
                    text="⚠️ The price you inserted is not valid. Try again:",
                    reply_markup=reply_markup,
                    edit=False,
                )
                return self.next.PRICE
        edit["limit_price"] = str(price)
        order_record = order.order_record
        try:
            with db.atomic():
                order_record.limit_price = edit["limit_price"]
                order_record.save()
        except Exception as e:
            self.command_error(update, context, text=f"Failed to update database record: {e}")
            return ConversationHandler.END
        finally:
            del context.user_data["editorder"]
        order.limit_price = price

        chat_message(
            update,
            context,
            text=f"✅ Alright, I will {order.type} when the price of {token.symbol} reaches "
            + f"{self.symbol_usd}{price:.4g} {self.symbol_bnb} per token.\n",
            edit=self.config.update_messages,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_editorder_tsl(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == edit["order_id"], token.orders))
        if update.message is None:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == "cancel":
                self.cancel_command(update, context)
                return ConversationHandler.END
            elif query.data == "None":  # disable trailing stop loss
                edit["trailing_stop"] = None
                order_record = order.order_record
                try:
                    with db.atomic():
                        order_record.trailing_stop = edit["trailing_stop"]
                        order_record.save()
                except Exception as e:
                    self.command_error(update, context, text=f"Failed to update database record: {e}")
                    return ConversationHandler.END
                finally:
                    del context.user_data["editorder"]
                order.trailing_stop = None
                chat_message(
                    update,
                    context,
                    text="✅ Alright, trailing stop loss has been disabled.",
                    edit=self.config.update_messages,
                )
                return ConversationHandler.END
            try:
                callback_rate = int(query.data)
            except ValueError:
                self.command_error(update, context, text="The callback rate is not recognized.")
                return ConversationHandler.END
        else:
            assert update.message and update.message.text
            try:
                callback_rate = int(update.message.text.strip())
            except ValueError:
                reply_markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("No trailing stop loss", callback_data="None"),
                            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                        ]
                    ]
                )
                chat_message(
                    update,
                    context,
                    text="⚠️ The callback rate is not recognized, try again:",
                    reply_markup=reply_markup,
                    edit=False,
                )
                return self.next.TRAILING

        edit["trailing_stop"] = callback_rate
        order_record = order.order_record
        try:
            with db.atomic():
                order_record.trailing_stop = edit["trailing_stop"]
                order_record.save()
        except Exception as e:
            self.command_error(update, context, text=f"Failed to update database record: {e}")
            return ConversationHandler.END
        finally:
            del context.user_data["editorder"]
        order.trailing_stop = edit["trailing_stop"]

        chat_message(
            update,
            context,
            text=f"✅ Alright, the order will use trailing stop loss with {callback_rate}% callback.",
            edit=self.config.update_messages,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_editorder_amount(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == edit["order_id"], token.orders))
        if update.message is None:  # we got a button callback, either cancel or fraction of token balance
            assert update.callback_query
            query = update.callback_query
            if query.data == "cancel":
                self.cancel_command(update, context)
                return ConversationHandler.END
            assert query.data is not None
            try:
                balance_fraction = Decimal(query.data)
            except Exception:
                self.command_error(update, context, text="The balance percentage is not recognized.")
                return ConversationHandler.END
            amount = balance_fraction * self.net.get_token_balance(token_address=token.address)
        else:
            assert update.message and update.message.text
            user_input = update.message.text.strip()
            if user_input.endswith("%"):
                try:
                    balance_fraction = Decimal(user_input[:-1]) / Decimal(100)
                    balance = (
                        self.net.get_token_balance(token_address=token.address)
                        if order.type == "sell"
                        else self.net.get_bnb_balance()
                    )
                    amount = balance_fraction * balance
                except Exception:
                    chat_message(
                        update, context, text="⚠️ The balance percentage is not recognized, try again:", edit=False
                    )
                    return self.next.AMOUNT
            else:
                try:
                    amount = Decimal(update.message.text.strip())
                except Exception:
                    chat_message(
                        update, context, text="⚠️ The amount you inserted is not valid. Try again:", edit=False
                    )
                    return self.next.AMOUNT
        decimals = 18 if order.type == "buy" else token.decimals
        unit = f"BNB worth of {token.symbol}" if order.type == "buy" else token.symbol
        edit["amount"] = int(amount * Decimal(10**decimals))
        order_record = order.order_record
        try:
            with db.atomic():
                order_record.amount = str(edit["amount"])
                order_record.save()
        except Exception as e:
            self.command_error(update, context, text=f"Failed to update database record: {e}")
            return ConversationHandler.END
        finally:
            del context.user_data["editorder"]
        order.amount = Wei(edit["amount"])

        chat_message(
            update,
            context,
            text=f"✅ Alright, order will {order.type} {format_token_amount(amount)} {unit}.",
            edit=self.config.update_messages,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_editorder_slippage(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == edit["order_id"], token.orders))
        if update.message is None:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == "cancel":
                self.cancel_command(update, context)
                return ConversationHandler.END
            try:
                slippage_percent = Decimal(query.data)
            except Exception:
                self.command_error(update, context, text="The slippage is not recognized.")
                return ConversationHandler.END
        else:
            assert update.message and update.message.text
            try:
                slippage_percent = Decimal(update.message.text.strip())
            except Exception:
                chat_message(update, context, text="⚠️ The slippage is not recognized, try again:", edit=False)
                return self.next.SLIPPAGE
        if slippage_percent < Decimal("0.01") or slippage_percent > 100:
            chat_message(update, context, text="⚠️ The slippage must be between 0.01 and 100, try again:", edit=False)
            return self.next.SLIPPAGE
        edit["slippage"] = f"{slippage_percent:.2f}"
        order_record = order.order_record
        try:
            with db.atomic():
                order_record.slippage = edit["slippage"]
                order_record.save()
        except Exception as e:
            self.command_error(update, context, text=f"Failed to update database record: {e}")
            return ConversationHandler.END
        finally:
            del context.user_data["editorder"]
        order.slippage = slippage_percent

        chat_message(
            update,
            context,
            text=f"✅ Alright, the order will use slippage of {slippage_percent}%.",
            edit=self.config.update_messages,
        )
        return ConversationHandler.END

    @check_chat_id
    def command_editorder_gas(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        edit = context.user_data["editorder"]
        token: TokenWatcher = self.parent.watchers[edit["token_address"]]
        order = next(filter(lambda o: o.order_record.id == edit["order_id"], token.orders))
        message = ""
        if update.message is None:
            assert update.callback_query
            query = update.callback_query
            assert query.data
            if query.data == "cancel":
                self.cancel_command(update, context)
                return ConversationHandler.END
            elif query.data == "None":
                edit["gas_price"] = None
                message = "✅ Alright, the order will use default network gas price."
            elif query.data.startswith("+"):
                try:
                    Decimal(query.data)
                except Exception:
                    self.command_error(update, context, text="Invalid gas price.")
                    return ConversationHandler.END
                edit["gas_price"] = query.data
                message = f"✅ Alright, the order will use default network gas price {query.data} Gwei."
            else:
                self.command_error(update, context, text="Invalid gas price.")
                return ConversationHandler.END
        else:
            assert update.message and update.message.text
            try:
                gas_price_gwei = Decimal(update.message.text.strip())
            except ValueError:
                chat_message(update, context, text="⚠️ The gas price is not recognized, try again:", edit=False)
                return self.next.GAS
            message = f"✅ Alright, the order will use {gas_price_gwei:.4g} Gwei for gas price."
            edit["gas_price"] = str(Web3.toWei(gas_price_gwei, unit="gwei"))

        order_record = order.order_record
        try:
            with db.atomic():
                order_record.gas_price = edit["gas_price"]
                order_record.save()
        except Exception as e:
            self.command_error(update, context, text=f"Failed to update database record: {e}")
            return ConversationHandler.END
        finally:
            del context.user_data["editorder"]
        order.gas_price = edit["gas_price"]

        chat_message(update, context, text=message, edit=self.config.update_messages)
        return ConversationHandler.END

    @check_chat_id
    def command_cancelorder(self, update: Update, context: CallbackContext):
        self.cancel_command(update, context)
        return ConversationHandler.END

    def get_type_name(self, order: OrderWatcher) -> str:
        return order.get_type_name()

    def get_type_icon(self, order: OrderWatcher) -> str:
        return order.get_type_icon()

    @check_chat_id
    def cancel_command(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        del context.user_data["editorder"]
        chat_message(update, context, text="⚠️ OK, I'm cancelling this command.", edit=self.config.update_messages)

    def command_error(self, update: Update, context: CallbackContext, text: str):
        assert context.user_data is not None
        del context.user_data["editorder"]
        chat_message(update, context, text=f"⛔️ {text}", edit=self.config.update_messages)
