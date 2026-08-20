const mineflayer = require('mineflayer');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const dbPath = path.resolve(__dirname, 'bot_database.db');

function connectDynamicBot() {
    const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READWRITE, (err) => {
        if (err) {
            setTimeout(connectDynamicBot, 10000);
            return;
        }
    });

    db.get("SELECT host, port, username FROM mc_config ORDER BY rowid DESC LIMIT 1", (err, row) => {
        db.close();

        if (!row) {
            console.log("⚠️ Chưa có cấu hình Minecraft. Đang chờ lệnh /setmc từ Discord...");
            setTimeout(connectDynamicBot, 10000);
            return;
        }

        console.log(`🔌 Đang kết nối đến server ${row.host}:${row.port} với tên ${row.username}...`);

        const bot = mineflayer.createBot({
            host: row.host,
            port: row.port,
            username: row.username,
            version: false
        });

        bot.on('spawn', () => {
            console.log("🤖 Bot Minecraft đã vào game thành công!");
            startCommandListener(bot);
        });

        bot.on('end', () => {
            console.log("⚠️ Bot Minecraft mất kết nối, đang kết nối lại sau 10s...");
            setTimeout(connectDynamicBot, 10000);
        });

        bot.on('error', (err) => {
            console.log("❌ Lỗi Minecraft Bot:", err.message);
        });
    });
}

function startCommandListener(bot) {
    setInterval(() => {
        const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READWRITE, (err) => {
            if (err) return;
        });

        db.get("SELECT * FROM mc_commands ORDER BY id ASC LIMIT 1", (err, row) => {
            if (row) {
                db.run("DELETE FROM mc_commands WHERE id = ?", [row.id], () => {
                    db.close();
                });

                if (row.command === 'chat' && row.args) {
                    bot.chat(row.args);
                } else if (row.command === 'come') {
                    bot.chat("Đang đến đây!");
                } else if (row.command === 'stop') {
                    bot.chat("Đã dừng mọi hành động.");
                } else if (row.command === 'status') {
                    bot.chat(`Máu hiện tại: ${bot.health}/20, Độ đói: ${bot.food}/20`);
                }
            } else {
                db.close();
            }
        });
    }, 2000);
}

connectDynamicBot();
createBot();
