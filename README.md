# LunchBot

LunchBot automates daily food ordering in one private Telegram group.

## What it does

- Detects a forwarded Russian-language menu.
- Extracts the date, meal names, portion price and delivery rule.
- Lets an admin confirm the menu before ordering opens.
- Uses Telegram buttons so each member can choose or change one meal.
- Reminds only registered members who have not ordered at 10:20, 10:35 and 10:50.
- Locks ordering at 11:00.
- Creates both a caterer-ready order and an internal summary.
- Matches receipt screenshots to the sender's order.
- Detects reused receipt images.
- Lets an admin verify or reject AI-matched payments.

## Important payment rule

Receipt AI is a pre-check, not bank confirmation. A screenshot can be edited. The bot uses these statuses:

- `Unpaid`
- `AI matched`
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

Turning off Group Privacy is required for automatic detection of forwarded menu messages and receipt images.

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
4. Every member presses **Register** once.

Telegram does not allow a bot to automatically retrieve every group member. Registration is necessary for targeted missing-order reminders.

## Daily use

1. Forward the caterer's menu to the private group.
2. The bot shows an extracted preview.
3. An admin presses **Confirm**.
4. Members choose a meal using the buttons.
5. The bot sends targeted reminders and closes orders at 11:00.
6. Members send payment screenshots after ordering.
7. The bot matches each screenshot to its sender and adds the payment status.

If a forwarded menu is not detected, an admin can reply to that message with `/menu`.

## Commands

- `/setup` — connect the current group; admin only.
- `/register` — register for reminders.
- `/menu` — parse the message being replied to; admin only.
- `/orders` — show the latest order summary.
- `/close` — close ordering immediately; admin only.
- `/help` — show instructions.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Data and privacy

- Data stays in the bot's SQLite database.
- Card and phone numbers are removed before menu text is stored.
- Receipt images are downloaded for analysis but are not saved by LunchBot.
- A SHA-256 fingerprint is stored to detect a duplicated receipt.
- If OpenAI receipt reading is enabled, the receipt image is sent to the OpenAI API with `store: false`.

The OpenAI implementation uses the Responses API with structured JSON output and image input, following the official API reference: https://developers.openai.com/api/reference/cli/resources/responses/methods/create
