# PancakeTrade - Limit orders and more for PancakeSwap on Binance Smart Chain

PancakeTrade helps you create limit orders and more for your BEP-20 tokens that swap against BNB on PancakeSwap.
The bot is controlled by Telegram so you can interact from anywhere.

![screenshot](screenshot.jpg)

## DISCLAIMER

This software is for educational purposes only. Do not risk money which you are afraid to lose. USE THE SOFTWARE AT
YOUR OWN RISK. THE AUTHORS AND ALL AFFILIATES ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.

We strongly recommend you to have coding and Python knowledge. Do not hesitate to read the source code and understand
the mechanism of this bot.

## Features

The bot provides a lot of convenience trading features including:

- Tokens balance and price shown in status messages
- Price tracking relative to buy transaction
- Ability to make buy and sell limit orders including trailing stop loss
- Automatic smart price selection in case PancakeSwap v1 and v2 LPs are available
- Automatic approval for selling
- Assign emoji to each token to differentiate them easily
- Default slippage set on a token basis for faster order creation
- Chart link for each token

## Requirements

In order to use this bot, you will need the following:

- A server or computer that can run the script continuously (linux or macOS, as well as WSL have been tested)
- Python version between 3.7.1 and 3.9.x
- [Poetry](https://github.com/python-poetry/poetry)
- A Telegram bot token (interact with [@BotFather](https://telegram.me/BotFather))
- Your Telegram chat ID/user ID (interact with [@userinfobot](https://telegram.me/userinfobot))
- Optional: `git` to clone this repository, you can also download the source from the github website.

## Quick Start

Before you start, make sure you have your Telegram Bot token + user ID available.
Initiate the chat with your bot on Telegram (click the "Start" button) to initialize the chat ID before you start the
bot the first time.

Run the following commands

```bash
git clone https://github.com/beeb/pancaketrade.git
cd pancaketrade
poetry install --no-dev
cp user_data/config.example.yml user_data/config.yml
```

Next, open the `config.yml` file inside the `user_data` folder with a text editor and populate the `wallet` and
`secrets` section.

The bot needs your wallet's private key in order to sign and execute orders on your behalf. You can either run the
command below and enter your private key in the prompt that will be shown, or you can provide an environment variable
named `WALLET_PK` that will be used by the bot.
The private key is **not** the same as the seed words. You need the 64-characters hexadecimal private key.

Run the bot:

```bash
poetry run trade
```

You will receive a notification in the Telegram chat after entering your private key. You can then start by adding your
tokens with the `/addtoken` chat command.

The other most useful command is the `/status` command that will display all your tokens and the existing orders.

## Configuration file

The script looks for a file named `config.yml` located inside the `user_data`folder by default.
You can pass another file path to the `trade` command as a positional argument.

The only parameter that is not self-explanatory is `min_pool_size_bnb`. Since PancakeSwap migrated to version 2, some
tokens have Liquidity Pairs (LP) on both v1 and v2. As a result, the price might be better for buying or selling on
one version versus the other.
However, sometimes the LP on a given version has very little liquidity, which means that the price is very volatile.
In order to avoid swapping on the version that has little liquidity, the bot checks that at least `min_pool_size_bnb`
is staked in the LP. If that's not the case, the bot will use the other version even if the price is worse.

The `update_messages` parameter will update the status messages every 30 seconds if set to `true`.
If you have trouble with the inline buttons not working, or messages not updating, disable `update_messages` as it has
happened in the past that some edge cases break message editing for a given bot token.

```yaml
---
bsc_rpc: 'https://bsc-dataseed.binance.org:443' # you can use any BSC RPC url you want
wallet: '0x0000000000000000000000000000000000000000' # insert your wallet adddress here
min_pool_size_bnb: 25 # PancakeSwap LPs that have less than 25 BNB will not be considered
monitor_interval: 5 # the script will check the token prices with this interval in seconds
update_messages: true # status messages will update periodically to show current values
secrets:
  telegram_token: 'enter_your_bot_token' # enter your Telegram Bot token
  admin_chat_id: 123456 # enter your chat ID/user ID to prevent other users to use the bot
```

## Run as a service

On systems that support `systemd`, you can use the included `pancaketrade.service` file to run this script as a service.

```bash
cp pancaketrade.service ~/.config/systemd/user/
# edit the new file in .config/systemd/user with your wallet private key
systemctl --user start pancaketrade.service
systemctl --user enable pancaketrade.service # run at launch
```

## Donations

If you feel like this project has helped you and you wish to donate to the developer, you can do so on the Ethereum or
Binance Smart Chain networks at the address:

`0x026E539B566DcFF02af980d938deCcb11255d519`

Thanks for your contribution!
