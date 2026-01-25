import os
import json
import re
import psycopg2
from datetime import datetime
from flask import Flask, request, jsonify
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def get_db_connection():
    """Подключение к базе данных"""
    database_url = os.environ.get('DATABASE_URL')
    
    if not database_url:
        logger.error("❌ DATABASE_URL не установлен!")
        return None
    
    try:
        conn = psycopg2.connect(database_url, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        return None

def init_database():
    """Инициализация таблиц в базе данных"""
    conn = get_db_connection()
    if not conn:
        logger.error("❌ Не могу инициализировать БД: нет подключения")
        return False
    
    try:
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trading_signals (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                signal VARCHAR(10) NOT NULL,
                price DECIMAL(15, 5) NOT NULL,
                strength DECIMAL(3, 1),
                timeframe VARCHAR(10),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE,
                status VARCHAR(20) DEFAULT 'active',
                raw_data JSONB
            )
        ''')
        
        cur.execute('CREATE INDEX IF NOT EXISTS idx_symbol ON trading_signals(symbol)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON trading_signals(timestamp)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_processed ON trading_signals(processed)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_status ON trading_signals(status)')
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info("✅ Таблицы базы данных инициализированы")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        return False

# Инициализируем базу при старте
init_database()

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "version": "3.0",
        "status": "running",
        "database": "connected" if get_db_connection() else "disconnected",
        "endpoints": {
            "webhook": "POST /webhook - Прием алертов из TradingView",
            "signals": "GET /signals - Все сигналы",
            "active_signals": "GET /signals/active - Непрочитанные сигналы",
            "health": "GET /health - Проверка здоровья",
            "test": "GET /test - Тестирование вебхука"
        }
    })

@app.route('/health', methods=['GET'])
def health():
    db_status = "connected" if get_db_connection() else "disconnected"
    return jsonify({
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "service": "TradingView Proxy",
        "database": db_status,
        "timestamp": datetime.now().isoformat(),
        "supported_formats": ["application/json", "form-data", "raw-text"]
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной эндпоинт для приема вебхуков из TradingView"""
    try:
        logger.info(f"📨 Входящий запрос от {request.remote_addr}")
        
        data = None
        
        # Пробуем получить данные в разных форматах
        if request.is_json:
            # Формат 1: application/json
            data = request.get_json()
            logger.info("📄 Формат: application/json")
        elif request.form:
            # Формат 2: form-data (обычный для TradingView)
            data = {
                "symbol": request.form.get('symbol'),
                "signal": request.form.get('signal'),
                "price": request.form.get('price'),
                "strength": request.form.get('strength', 8.5),
                "timeframe": request.form.get('timeframe', '5m')
            }
            logger.info("📄 Формат: form-data")
        else:
            # Формат 3: raw текст
            try:
                raw_text = request.get_data(as_text=True)
                logger.info(f"📄 Raw данные: {raw_text[:200]}")
                
                # Пробуем распарсить как JSON
                try:
                    data = json.loads(raw_text)
                except:
                    # Пробуем найти JSON в тексте
                    json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                        except:
                            pass
                
                # Если не нашли JSON, пробуем извлечь ключевые поля
                if not data:
                    data = {}
                    # Ищем symbol
                    symbol_match = re.search(r'"symbol":\s*"([^"]+)"', raw_text)
                    if symbol_match:
                        data["symbol"] = symbol_match.group(1)
                    
                    # Ищем signal
                    signal_match = re.search(r'"signal":\s*"([^"]+)"', raw_text)
                    if signal_match:
                        data["signal"] = signal_match.group(1)
                    
                    # Ищем price
                    price_match = re.search(r'"price":\s*([\d.]+)', raw_text)
                    if price_match:
                        data["price"] = float(price_match.group(1))
        
        # Если данные не получены
        if not data:
            logger.error("❌ Не удалось извлечь данные из запроса")
            return jsonify({
                "status": "error",
                "message": "Не удалось извлечь данные из запроса",
                "tip": "Отправьте данные в формате JSON: {\"symbol\":\"BTCUSDT\",\"signal\":\"LONG\",\"price\":50000}"
            }), 400
        
        # Проверяем обязательные поля
        required = ['symbol', 'signal', 'price']
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Приводим данные к стандартному формату
        symbol = str(data['symbol']).upper().strip()
        signal = str(data['signal']).upper().strip()
        
        try:
            price = float(data['price'])
        except:
            logger.error(f"❌ Некорректная цена: {data['price']}")
            return jsonify({"error": f"Некорректная цена: {data['price']}"}), 400
        
        strength = float(data.get('strength', 8.5))
        timeframe = data.get('timeframe', '5m')
        
        logger.info(f"✅ Данные верифицированы: {symbol} {signal} @ {price}")
        
        # Сохраняем в базу данных
        conn = get_db_connection()
        if not conn:
            logger.error("❌ Не могу подключиться к базе данных")
            return jsonify({
                "status": "warning",
                "message": "Signal received but database not connected"
            }), 200
        
        try:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO trading_signals 
                (symbol, signal, price, strength, timeframe, raw_data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, timestamp
            ''', (symbol, signal, price, strength, timeframe, json.dumps(data)))
            
            signal_id, timestamp = cur.fetchone()
            conn.commit()
            
            logger.info(f"💾 Сигнал сохранен в БД с ID: {signal_id}")
            
            cur.close()
            conn.close()
            
            return jsonify({
                "status": "success",
                "message": "Signal saved to database",
                "signal_id": signal_id,
                "data": {
                    "id": signal_id,
                    "symbol": symbol,
                    "signal": signal,
                    "price": price,
                    "strength": strength,
                    "timeframe": timeframe,
                    "timestamp": timestamp.isoformat() if timestamp else datetime.now().isoformat()
                }
            }), 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении в БД: {e}")
            conn.rollback()
            return jsonify({"error": f"Database error: {str(e)}"}), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/signals', methods=['GET'])
def get_signals():
    """Получить все сохраненные сигналы"""
    try:
        limit = request.args.get('limit', 50, type=int)
        symbol = request.args.get('symbol')
        
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database not connected",
                "signals": []
            }), 200
        
        cur = conn.cursor()
        
        if symbol:
            cur.execute('''
                SELECT id, symbol, signal, price, strength, timeframe, timestamp, processed, raw_data
                FROM trading_signals
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (symbol.upper(), limit))
        else:
            cur.execute('''
                SELECT id, symbol, signal, price, strength, timeframe, timestamp, processed, raw_data
                FROM trading_signals
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (limit,))
        
        signals = cur.fetchall()
        cur.close()
        conn.close()
        
        result = []
        for sig in signals:
            result.append({
                "id": sig[0],
                "symbol": sig[1],
                "signal": sig[2],
                "price": float(sig[3]),
                "strength": float(sig[4]) if sig[4] else None,
                "timeframe": sig[5],
                "timestamp": sig[6].isoformat() if sig[6] else None,
                "processed": sig[7],
                "raw_data": sig[8]
            })
        
        return jsonify({
            "status": "success",
            "count": len(result),
            "signals": result
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения сигналов: {e}")
        return jsonify({"error": str(e), "signals": []}), 500

@app.route('/signals/active', methods=['GET'])
def get_active_signals():
    """Получить только непрочитанные сигналы"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database not connected",
                "signals": []
            }), 200
        
        cur = conn.cursor()
        cur.execute('''
            SELECT id, symbol, signal, price, strength, timeframe, timestamp, raw_data
            FROM trading_signals
            WHERE processed = FALSE
            ORDER BY timestamp DESC
        ''')
        
        signals = cur.fetchall()
        cur.close()
        conn.close()
        
        result = []
        for sig in signals:
            result.append({
                "id": sig[0],
                "symbol": sig[1],
                "signal": sig[2],
                "price": float(sig[3]),
                "strength": float(sig[4]) if sig[4] else None,
                "timeframe": sig[5],
                "timestamp": sig[6].isoformat() if sig[6] else None,
                "raw_data": sig[7]
            })
        
        return jsonify({
            "status": "success",
            "count": len(result),
            "signals": result
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения активных сигналов: {e}")
        return jsonify({"error": str(e), "signals": []}), 500

@app.route('/signals/<int:signal_id>/mark_processed', methods=['POST'])
def mark_signal_processed(signal_id):
    """Пометить сигнал как обработанный"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database not connected"
            }), 200
        
        cur = conn.cursor()
        cur.execute('''
            UPDATE trading_signals
            SET processed = TRUE
            WHERE id = %s
            RETURNING id, symbol, signal
        ''', (signal_id,))
        
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if not updated:
            return jsonify({
                "status": "error",
                "message": f"Signal with ID {signal_id} not found"
            }), 404
        
        return jsonify({
            "status": "success",
            "message": f"Signal {signal_id} ({updated[1]} {updated[2]}) marked as processed"
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка обновления сигнала: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/test', methods=['GET'])
def test_page():
    """Страница для тестирования"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test TradingView Webhook</title>
        <style>
            body { font-family: Arial; max-width: 600px; margin: 40px auto; }
            .form-group { margin: 15px 0; }
            label { display: block; margin-bottom: 5px; }
            input, select { width: 100%; padding: 8px; }
            button { background: #007bff; color: white; border: none; padding: 10px 20px; cursor: pointer; }
            .result { margin-top: 20px; padding: 15px; border-radius: 5px; }
            .success { background: #d4edda; color: #155724; }
            .error { background: #f8d7da; color: #721c24; }
        </style>
        <script>
            async function sendTest() {
                const symbol = document.getElementById('symbol').value;
                const signal = document.getElementById('signal').value;
                const price = document.getElementById('price').value;
                
                const data = {
                    symbol: symbol,
                    signal: signal,
                    price: parseFloat(price),
                    strength: 9.0,
                    timeframe: '5m'
                };
                
                try {
                    const response = await fetch('/webhook', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(data)
                    });
                    
                    const result = await response.json();
                    const resultDiv = document.getElementById('result');
                    
                    if (response.ok) {
                        resultDiv.className = 'result success';
                        resultDiv.innerHTML = `
                            <h3>✅ Success!</h3>
                            <p>Signal ID: ${result.signal_id}</p>
                            <p>Message: ${result.message}</p>
                            <p><a href="/signals">View all signals</a></p>
                        `;
                    } else {
                        resultDiv.className = 'result error';
                        resultDiv.innerHTML = `
                            <h3>❌ Error</h3>
                            <p>Status: ${response.status}</p>
                            <p>Message: ${result.error || result.message}</p>
                        `;
                    }
                } catch (error) {
                    document.getElementById('result').className = 'result error';
                    document.getElementById('result').innerHTML = `<h3>❌ Network Error</h3><p>${error}</p>`;
                }
            }
        </script>
    </head>
    <body>
        <h1>🔧 Test TradingView Webhook</h1>
        
        <div class="form-group">
            <label>Symbol:</label>
            <input type="text" id="symbol" value="BTCUSDT" required>
        </div>
        
        <div class="form-group">
            <label>Signal:</label>
            <select id="signal">
                <option value="LONG">LONG</option>
                <option value="SHORT">SHORT</option>
            </select>
        </div>
        
        <div class="form-group">
            <label>Price:</label>
            <input type="number" id="price" value="50000" step="0.01" required>
        </div>
        
        <button onclick="sendTest()">📨 Send Test Webhook</button>
        
        <div id="result" class="result"></div>
        
        <hr>
        
        <h3>📝 TradingView Setup:</h3>
        <p><strong>Webhook URL:</strong></p>
        <code>https://tradingview-proxy-h71n.onrender.com/webhook</code>
        
        <p><strong>Message Format (JSON):</strong></p>
        <code>{"symbol":"{{ticker}}","signal":"LONG","price":{{close}}}</code>
        
        <h3>🔗 Useful Links:</h3>
        <ul>
            <li><a href="/health">/health</a> - Check service health</li>
            <li><a href="/signals">/signals</a> - View all signals</li>
            <li><a href="/signals/active">/signals/active</a> - Active signals</li>
        </ul>
    </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Сервис запущен на порту {port}")
    logger.info(f"📡 Webhook URL: https://tradingview-proxy-h71n.onrender.com/webhook")
    logger.info(f"💾 Database: {'connected' if get_db_connection() else 'disconnected'}")
    app.run(host='0.0.0.0', port=port, debug=False)
