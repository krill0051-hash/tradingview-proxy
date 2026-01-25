import os
import json
import re
import psycopg2
from datetime import datetime
from flask import Flask, request, jsonify
import logging
import requests

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
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>TradingView Proxy API</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            .header { background: #007bff; color: white; padding: 20px; border-radius: 10px; }
            .card { background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 5px; padding: 15px; margin: 15px 0; }
            .endpoint { background: #e9ecef; padding: 10px; margin: 10px 0; border-radius: 5px; }
            code { background: #2b2b2b; color: #f8f8f2; padding: 2px 5px; border-radius: 3px; }
            .btn { background: #28a745; color: white; padding: 10px 20px; border: none; border-radius: 5px; text-decoration: none; display: inline-block; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 TradingView Proxy API</h1>
            <p>Version 3.0 | Universal Webhook Receiver</p>
        </div>
        
        <div class="card">
            <h2>📊 System Status</h2>
            <p>Database: <strong>''' + ("✅ Connected" if get_db_connection() else "❌ Disconnected") + '''</strong></p>
            <p>Webhook URL: <code>https://tradingview-proxy-h71n.onrender.com/webhook</code></p>
        </div>
        
        <div class="card">
            <h2>🔗 Endpoints</h2>
            
            <div class="endpoint">
                <h3>POST /webhook</h3>
                <p>Прием алертов из TradingView (поддерживает все форматы)</p>
                <code>{"symbol":"BTCUSDT","signal":"LONG","price":50000}</code>
            </div>
            
            <div class="endpoint">
                <h3>GET /signals</h3>
                <p>Все сохраненные сигналы</p>
                <a href="/signals" class="btn">View Signals</a>
            </div>
            
            <div class="endpoint">
                <h3>GET /signals/active</h3>
                <p>Непрочитанные сигналы</p>
                <a href="/signals/active" class="btn">Active Signals</a>
            </div>
            
            <div class="endpoint">
                <h3>GET /health</h3>
                <p>Проверка здоровья системы</p>
                <a href="/health" class="btn">Health Check</a>
            </div>
            
            <div class="endpoint">
                <h3>GET /test</h3>
                <p>Тестирование вебхука через браузер</p>
                <a href="/test" class="btn">Test Webhook</a>
            </div>
        </div>
        
        <div class="card">
            <h2>🎯 TradingView Setup</h2>
            <p><strong>Webhook URL:</strong></p>
            <code>https://tradingview-proxy-h71n.onrender.com/webhook</code>
            
            <p><strong>Message Format (JSON):</strong></p>
            <code>{"symbol":"{{ticker}}","signal":"LONG","price":{{close}}}</code>
            
            <p><strong>Alternative Format (Text):</strong></p>
            <code>symbol={{ticker}}&signal=LONG&price={{close}}</code>
        </div>
        
        <div class="card">
            <h2>📞 Quick Test</h2>
            <p>Test from PowerShell:</p>
            <code>curl -Method POST -Uri "https://tradingview-proxy-h71n.onrender.com/webhook" -ContentType "application/json" -Body '{"symbol":"TEST","signal":"LONG","price":1000}'</code>
        </div>
    </body>
    </html>
    '''

@app.route('/health', methods=['GET'])
def health():
    db_status = "connected" if get_db_connection() else "disconnected"
    return jsonify({
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "service": "TradingView Proxy",
        "version": "3.0",
        "database": db_status,
        "timestamp": datetime.now().isoformat(),
        "supported_formats": ["application/json", "form-data", "raw-text"],
        "note": "Universal webhook receiver - accepts any format from TradingView"
    })

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    """Универсальный обработчик вебхуков для TradingView"""
    try:
        logger.info(f"📨 Входящий запрос от {request.remote_addr}")
        logger.info(f"📋 Метод: {request.method}")
        logger.info(f"📦 Заголовки: {dict(request.headers)}")
        
        data = None
        
        # Если GET запрос - показываем инструкцию
        if request.method == 'GET':
            return jsonify({
                "status": "info",
                "message": "Это вебхук-эндпоинт для TradingView. Используйте POST запрос.",
                "example": {
                    "url": "https://tradingview-proxy-h71n.onrender.com/webhook",
                    "method": "POST",
                    "content-type": "application/json",
                    "body": {"symbol": "BTCUSDT", "signal": "LONG", "price": 50000}
                }
            }), 200
        
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
            logger.info("📄 Формат: form-data (x-www-form-urlencoded)")
        else:
            # Формат 3: raw текст (пробуем распарсить как JSON)
            try:
                raw_text = request.get_data(as_text=True)
                logger.info(f"📄 Raw данные: {raw_text[:500]}")
                
                # Пробуем распарсить как JSON
                try:
                    data = json.loads(raw_text)
                    logger.info("📄 Формат: raw text (parsed as JSON)")
                except:
                    # Пробуем извлечь JSON из текста
                    json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            logger.info("📄 JSON найден и распарсен в raw text")
                        except:
                            pass
                
                # Если не нашли JSON, пробуем извлечь ключевые поля
                if not data:
                    data = {}
                    # Ищем symbol
                    symbol_match = re.search(r'"symbol":\s*"([^"]+)"', raw_text)
                    if symbol_match:
                        data["symbol"] = symbol_match.group(1)
                    else:
                        # Пробуем другой формат
                        symbol_match = re.search(r'symbol=([^&\s]+)', raw_text)
                        if symbol_match:
                            data["symbol"] = symbol_match.group(1)
                    
                    # Ищем signal
                    signal_match = re.search(r'"signal":\s*"([^"]+)"', raw_text)
                    if signal_match:
                        data["signal"] = signal_match.group(1)
                    else:
                        signal_match = re.search(r'signal=([^&\s]+)', raw_text)
                        if signal_match:
                            data["signal"] = signal_match.group(1)
                    
                    # Ищем price
                    price_match = re.search(r'"price":\s*([\d.]+)', raw_text)
                    if price_match:
                        data["price"] = float(price_match.group(1))
                    else:
                        price_match = re.search(r'price=([\d.]+)', raw_text)
                        if price_match:
                            data["price"] = float(price_match.group(1))
                    
                    if data:
                        logger.info("📄 Данные извлечены из raw text")
        
        # Если данные не получены
        if not data:
            logger.error("❌ Не удалось извлечь данные из запроса")
            raw_content = request.get_data(as_text=True)
            logger.info(f"📦 Полные raw данные ({len(raw_content)} chars): {raw_content[:1000]}")
            
            return jsonify({
                "status": "error",
                "message": "Не удалось извлечь данные из запроса",
                "tip": "Отправьте данные в формате JSON: {\"symbol\":\"BTCUSDT\",\"signal\":\"LONG\",\"price\":50000}",
                "received_content": raw_content[:500],
                "content_type": request.content_type
            }), 400
        
        # Валидация обязательных полей
        if 'symbol' not in data or 'signal' not in data or 'price' not in data:
            missing = []
            if 'symbol' not in data: missing.append('symbol')
            if 'signal' not in data: missing.append('signal')
            if 'price' not in data: missing.append('price')
            
            logger.error(f"❌ Отсутствуют обязательные поля: {missing}")
            logger.error(f"📦 Полученные данные: {data}")
            
            return jsonify({
                "status": "error",
                "message": f"Отсутствуют обязательные поля: {missing}",
                "data_received": data
            }), 400
        
        # Приводим данные к стандартному формату
        symbol = str(data['symbol']).upper().replace('"', '').strip()
        signal = str(data['signal']).upper().replace('"', '').strip()
        
        try:
            price = float(data['price'])
        except:
            logger.error(f"❌ Некорректная цена: {data['price']}")
            return jsonify({
                "status": "error",
                "message": f"Некорректная цена: {data['price']}"
            }), 400
        
        strength = float(data.get('strength', 8.5))
        timeframe = data.get('timeframe', '5m')
        
        signal_data = {
            "symbol": symbol,
            "signal": signal,
            "price": price,
            "strength": strength,
            "timeframe": timeframe,
            "timestamp": datetime.now().isoformat() + "Z",
            "status": "active",
            "processed": False,
            "raw_data": data
        }
        
        logger.info(f"✅ Данные верифицированы: {signal_data['symbol']} {signal_data['signal']} @ {signal_data['price']}")
        
        # Сохраняем в базу данных
        try:
            conn = get_db_connection()
            if not conn:
                logger.error("❌ Не могу подключиться к базе данных")
                return jsonify({
                    "status": "warning",
                    "message": "Signal received but database not connected",
                    "data": signal_data
                }), 200
            
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO trading_signals 
                (symbol, signal, price, strength, timeframe, status, processed, raw_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, timestamp
            ''', (
                signal_data['symbol'], 
                signal_data['signal'], 
                signal_data['price'],
                signal_data['strength'], 
                signal_data['timeframe'],
                signal_data['status'],
                signal_data['processed'],
                json.dumps(signal_data['raw_data'])
            ))
            
            signal_id, timestamp = cur.fetchone()
            conn.commit()
            
            signal_data['id'] = signal_id
            signal_data['database_timestamp'] = timestamp.isoformat() if timestamp else None
            
            logger.info(f"💾 Сигнал сохранен в БД с ID: {signal_id}")
            
            cur.close()
            conn.close()
            
            # Успешный ответ
            return jsonify({
                "status": "success",
                "message": f"Signal received and saved (ID: {signal_id})",
                "signal_id": signal_id,
                "data": signal_data,
                "cloud_url": f"https://tradingview-proxy-h71n.onrender.com/signals/{signal_id}",
                "formats_accepted": [
                    "application/json",
                    "form-data", 
                    "raw-text",
                    "TradingView default"
                ]
            }), 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении в БД: {e}")
            if 'conn' in locals():
                conn.rollback()
                conn.close()
            return jsonify({
                "status": "error",
                "message": f"Database error: {str(e)}",
                "data": signal_data
            }), 500
            
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка обработки вебхука: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        return jsonify({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "tip": "Проверьте формат данных. TradingView должен отправлять: {\"symbol\":\"BTCUSDT\",\"signal\":\"LONG\",\"price\":50000}",
            "example_url": "https://tradingview-proxy-h71n.onrender.com/test"
        }), 500

@app.route('/test', methods=['GET', 'POST'])
def test_webhook():
    """Страница для тестирования вебхука"""
    if request.method == 'POST':
        # Симулируем запрос от TradingView
        test_data = {
            "symbol": request.form.get('symbol', 'BTCUSDT'),
            "signal": request.form.get('signal', 'LONG'),
            "price": float(request.form.get('price', 50000)),
            "strength": float(request.form.get('strength', 9.0)),
            "timeframe": request.form.get('timeframe', '5m')
        }
        
        # Отправляем тестовый запрос
        try:
            response = requests.post(
                f"{request.url_root.rstrip('/')}/webhook",
                json=test_data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            result = {
                "test_request": test_data,
                "response_status": response.status_code,
                "response_data": response.json() if response.headers.get('content-type') == 'application/json' else response.text
            }
            
            # HTML с результатом
            return f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Test Result</title>
                <style>
                    body {{ font-family: Arial; max-width: 800px; margin: 40px auto; padding: 20px; }}
                    .success {{ background: #d4edda; color: #155724; padding: 20px; border-radius: 5px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 20px; border-radius: 5px; }}
                    pre {{ background: #f8f9fa; padding: 15px; border-radius: 5px; overflow-x: auto; }}
                    .btn {{ background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; text-decoration: none; display: inline-block; margin: 10px 5px; }}
                </style>
            </head>
            <body>
                <h1>{"✅ Тест успешен!" if response.status_code == 200 else "❌ Тест не пройден"}</h1>
                
                <div class="{'success' if response.status_code == 200 else 'error'}">
                    <h3>Статус: {response.status_code}</h3>
                </div>
                
                <h3>Отправленные данные:</h3>
                <pre>{json.dumps(test_data, indent=2)}</pre>
                
                <h3>Ответ сервера:</h3>
                <pre>{json.dumps(result, indent=2)}</pre>
                
                <a href="/test" class="btn">Еще один тест</a>
                <a href="/" class="btn">Главная</a>
                <a href="/signals" class="btn">Просмотр сигналов</a>
            </body>
            </html>
            '''
            
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    # HTML форма для тестирования
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test TradingView Webhook</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }
            .form-group { margin: 15px 0; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input, select { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
            button { background: #28a745; color: white; border: none; padding: 12px 24px; border-radius: 5px; cursor: pointer; font-size: 16px; width: 100%; }
            .info-box { background: #e7f3ff; border-left: 4px solid #2196F3; padding: 15px; margin: 20px 0; }
            .code { background: #2b2b2b; color: #f8f8f2; padding: 15px; border-radius: 5px; overflow-x: auto; font-family: monospace; }
            .tabs { display: flex; margin-bottom: 20px; }
            .tab { padding: 10px 20px; background: #f1f1f1; border: none; cursor: pointer; flex: 1; text-align: center; }
            .tab.active { background: #007bff; color: white; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
        </style>
        <script>
            function showTab(tabId) {
                // Hide all tab contents
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
                
                // Show selected tab
                document.getElementById(tabId).classList.add('active');
                event.target.classList.add('active');
            }
        </script>
    </head>
    <body>
        <h1>🔧 Test TradingView Webhook</h1>
        
        <div class="tabs">
            <button class="tab active" onclick="showTab('simple-test')">Simple Test</button>
            <button class="tab" onclick="showTab('raw-test')">Raw Test</button>
            <button class="tab" onclick="showTab('curl-test')">cURL Test</button>
        </div>
        
        <div id="simple-test" class="tab-content active">
            <form method="POST">
                <div class="form-group">
                    <label>Symbol:</label>
                    <input type="text" name="symbol" value="BTCUSDT" required>
                </div>
                
                <div class="form-group">
                    <label>Signal:</label>
                    <select name="signal">
                        <option value="LONG" selected>LONG</option>
                        <option value="SHORT">SHORT</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label>Price:</label>
                    <input type="number" step="0.01" name="price" value="50000" required>
                </div>
                
                <div class="form-group">
                    <label>Strength (1-10):</label>
                    <input type="number" step="0.1" min="1" max="10" name="strength" value="9.0">
                </div>
                
                <div class="form-group">
                    <label>Timeframe:</label>
                    <select name="timeframe">
                        <option value="1m">1 Minute</option>
                        <option value="5m" selected>5 Minutes</option>
                        <option value="15m">15 Minutes</option>
                        <option value="1h">1 Hour</option>
                    </select>
                </div>
                
                <button type="submit">📨 Send Test Webhook</button>
            </form>
        </div>
        
        <div id="raw-test" class="tab-content">
            <div class="info-box">
                <h3>Test with Raw Data</h3>
                <p>This simulates exactly what TradingView sends.</p>
            </div>
            
            <form method="POST" action="/webhook">
                <div class="form-group">
                    <label>Raw JSON Data:</label>
                    <textarea name="raw_data" rows="8" style="width: 100%; font-family: monospace;">{"symbol":"BTCUSDT","signal":"LONG","price":50000,"strength":9.5,"timeframe":"5m"}</textarea>
                </div>
                <button type="submit">📨 Send Raw JSON</button>
            </form>
            
            <div class="form-group">
                <label>Form-encoded Data:</label>
                <form method="POST" action="/webhook">
                    <input type="hidden" name="symbol" value="ETHUSDT">
                    <input type="hidden" name="signal" value="SHORT">
                    <input type="hidden" name="price" value="2800">
                    <button type="submit">📨 Send Form Data (ETHUSDT SHORT)</button>
                </form>
            </div>
        </div>
        
        <div id="curl-test" class="tab-content">
            <div class="info-box">
                <h3>Test with cURL/PowerShell</h3>
                <p>Copy and paste these commands to test from command line.</p>
            </div>
            
            <h4>PowerShell:</h4>
            <div class="code">
$body = @{symbol="BTCUSDT"; signal="LONG"; price=50000} | ConvertTo-Json<br>
Invoke-RestMethod -Method POST -Uri "https://tradingview-proxy-h71n.onrender.com/webhook" -ContentType "application/json" -Body $body
            </div>
            
            <h4>cURL:</h4>
            <div class="code">
curl -X POST https://tradingview-proxy-h71n.onrender.com/webhook \<br>
  -H "Content-Type: application/json" \<br>
  -d '{"symbol":"BTCUSDT","signal":"LONG","price":50000}'
            </div>
            
            <h4>Windows CMD:</h4>
            <div class="code">
curl -X POST https://tradingview-proxy-h71n.onrender.com/webhook ^<br>
  -H "Content-Type: application/json" ^<br>
  -d "{\"symbol\":\"BTCUSDT\",\"signal\":\"LONG\",\"price\":50000}"
            </div>
        </div>
        
        <div class="info-box">
            <h3>📝 Форматы для TradingView:</h3>
            
            <h4>JSON (рекомендуется):</h4>
            <div class="code">
{"symbol":"{{ticker}}","signal":"LONG","price":{{close}}}
            </div>
            
            <h4>Text/Form:</h4>
            <div class="code">
symbol={{ticker}}&signal=LONG&price={{close}}
            </div>
            
            <h4>Пример сигналов:</h4>
            <div class="code">
BTCUSDT LONG @ 50000<br>
ETHUSDT SHORT @ 2800<br>
SOLUSDT LONG @ 100
            </div>
        </div>
        
        <div style="margin-top: 30px; text-align: center;">
            <a href="/" style="margin-right: 15px;">🏠 Главная</a>
            <a href="/health" style="margin-right: 15px;">❤️ Health Check</a>
            <a href="/signals">📊 Все сигналы</a>
        </div>
    </body>
    </html>
    '''
