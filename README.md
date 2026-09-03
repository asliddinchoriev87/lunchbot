# LunchBot

LunchBot automates daily food ordering in one private Telegram group.
LunchBot can also work in multiple private groups with separate menus and orders.

## What it does

- Detects a forwarded Russian-language menu.
- Extracts the date, meal names, portion price and delivery rule.
- Lets an admin confirm the menu before ordering opens.
- Keeps one live dashboard in the group with the menu, names, orders and payment statuses.
- Moves meal selection, receipts and personal status into each member's private bot chat.
- Sends private reminders only to members who have not ordered at 10:20, 10:35 and 10:50.
- Keeps ordering open until an admin closes it.
- Ignores repeated Telegram updates and repeated copies of the same menu within 10 minutes.
- Treats a forwarded menu album as one menu and publishes all food photos to the group.
- Shows normal members only one group button: **Choose meal — private chat**.
- Gives admins a separate private control panel with full orders, payment review and closing.
- Automatically registers a group when the bot is added as an administrator.
- Publishes a privately forwarded menu to every connected group where the sender is an admin after one confirmation.
- Persists Telegram's update position so restarts do not replay old menus.
- Creates both a caterer-ready order and an internal summary.
- Matches receipt screenshots to the sender's order.
- Detects reused receipt images.
- Keeps submitted receipts pending until an admin verifies or rejects them.

## Important payment rule

Receipt AI is a pre-check, not bank confirmation. A screenshot can be edited. The bot uses these statuses:

- `Unpaid`
- `Needs review`
- `Verified by admin`
- `Rejected`

True bank-level automatic verification requires a payment-provider transaction API or webhook.

## Setup without coding

### Recommended hosting: Render

The repository includes `render.yaml` for a Docker background worker in Frankfurt with a 1 GB persistent disk mounted at `/data`. In Render, create a new **Blueprint**, connect this repository and provide the three private values when prompted:

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `PAYMENT_RECIPIENT_NAMES`

Review Render's displayed monthly cost before applying the Blueprint. A persistent disk requires a paid Render service.

### 1. Configure the bot in BotFather

1. Open `@BotFather` in Telegram.
2. Send `/mybots` and select your bot.
3. Open **Bot Settings → Group Privacy → Turn off**.
4. Never publish or send your bot token in a group.

Turning off Group Privacy is required for automatic detection of forwarded menu messages.

### 2. Add private settings

Copy `.env.example` to `.env` and replace the placeholder values:

- `TELEGRAM_BOT_TOKEN`: token from BotFather.
- `OPENAI_API_KEY`: optional for menu fallback and receipt reading.
- `PAYMENT_RECIPIENT_NAMES`: valid payment-recipient names separated by commas.

Do not put card numbers in `.env`; the bot only needs recipient names.

### 3. Start with Docker

```bash
docker build -t lunchbot .
docker run -d --name lunchbot --restart unless-stopped \
  --env-file .env -v lunchbot-data:/data lunchbot
```

The `/data` volume preserves orders and payments after a restart.

### 4. Connect the Telegram group

1. Add the bot to the private group.
2. Make the bot an admin. It only needs permission to read and send messages.
3. An admin sends `/setup` in the group.
4. Every member presses **Open bot privately** once and starts the bot.

Telegram does not allow a bot to automatically retrieve every group member or message a person before they start the bot. Opening the private chat once is necessary for private reminders.

Adding the bot as an administrator is normally enough to register a new group. `/setup` remains available as a manual fallback.

## Daily use

1. An admin forwards the caterer's menu to the bot's private chat.
2. The bot shows the extracted preview privately to that admin.
3. The admin presses **Confirm**. The bot publishes the menu photos and the single live group dashboard.
4. Members see only **Choose meal — private chat** in the group and select food inside the bot.
5. After selecting, the bot immediately shows a simple payment step and asks for the receipt image.
6. The bot updates the same group dashboard instead of posting new order messages.
7. Private reminders are sent while ordering remains open.
8. Members send payment screenshots to the bot's private chat.
9. The bot updates payment status beside each member's name on the group dashboard.
10. Admins use `/admin` privately to see full orders with payment statuses, review receipts and close ordering.

If a forwarded menu is not detected, an admin can reply to that message with `/menu`.

## Commands

- `/setup` — connect the current group; admin only.
- `/register` — open private registration instructions.
- `/menu` — parse the message being replied to; admin only.
- `/orders` — show personal order status in private chat; refresh the dashboard in the group.
- `/admin` — open private admin controls; admin only.
- `/close` — close the current order; admin only.
- `/help` — show instructions.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Data and privacy

- Data stays in the bot's SQLite database.
- Card and phone numbers are removed before menu text is stored.
- Receipt images are downloaded for analysis but are not saved as files by LunchBot.
- A Telegram file reference is stored so an admin can review the receipt privately.
- A SHA-256 fingerprint is stored to detect a duplicated receipt.
- If OpenAI receipt reading is enabled, the receipt image is sent to the OpenAI API with `store: false`.

The OpenAI implementation uses the Responses API with structured JSON output and image input, following the official API reference: https://developers.openai.com/api/reference/cli/resources/responses/methods/create
