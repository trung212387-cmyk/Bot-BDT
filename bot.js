const mineflayer = require('mineflayer');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const dbPath = path.resolve(__dirname, 'bot_database.db');

function createBot() {
    const bot = mineflayer.createBot({
        host: 'ip_server_cua_ban.com', // Thay IP server Minecraft của bạn vào đây
        username: 'TenNhanVat',       // Tên tài khoản bot Minecraft
        version: false
    });

    bot.on('spawn', () => {
        console.log("🤖 Bot Minecraft đã vào game thành công!");
        startCommandListener(bot);
    });

    bot.on('end', () => {
        console.log("⚠️ Bot Minecraft mất kết nối, đang kết nối lại sau 10s...");
        setTimeout(createBot, 10000);
    });

    bot.on('error', (err) => {
        console.log("❌ Lỗi Minecraft Bot:", err);
    });
}

function startCommandListener(bot) {
    setInterval(() => {
        const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READWRITE, (err) => {
            if (err) return;
        });

        db.get("SELECT * FROM mc_commands ORDER BY id ASC LIMIT 1", (err, row) => {
            if (row) {
                db.run("DELETE FROM mc_commands WHERE id = ?", [row.id]);

                if (row.command === 'chat' && row.args) {
                    bot.chat(row.args);
                } else if (row.command === 'come') {
                    bot.chat("Đang đến đây!");
                } else if (row.command === 'stop') {
                    bot.chat("Đã dừng mọi hành động.");
                } else if (row.command === 'status') {
                    bot.chat(`Máu hiện tại: ${bot.health}/20, Độ đói: ${bot.food}/20`);
                }
            }
            db.close();
        });
    }, 2000);
}

createBot();