import os
import json
import psycopg2
import urllib.parse
from datetime import datetime
from flask import Flask, request, jsonify
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def get_db_connection():
    """Подключение к базе данных с обработкой ошибок"""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logger.error("DATABASE_URL not set")
            return None
        
        # Исправляем URL для psycopg2
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        conn = psycopg2.connect(database_url, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"❌ DB connection error: {e}")
        return None

def init_database():
    """Инициализация таблиц базы данных"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # 1. Основная таблица сигналов (увеличиваем длину signal до 50)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS trading_signals (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(50) NOT NULL,
                signal VARCHAR(50) NOT NULL,
                price DECIMAL(20, 8) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE,
                source VARCHAR(50) DEFAULT 'unknown'
            )
        ''')
        
        # 2. Расширенная таблица KIRA (храним ВСЕ данные)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS kiria_full_signals (
                id SERIAL PRIMARY KEY,
                signal_id INTEGER REFERENCES trading_signals(id) ON DELETE CASCADE,
                full_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 3. Индексы для быстрого поиска
        cur.execute('CREATE INDEX IF NOT EXISTS idx_kiria_signal_id ON kiria_full_signals(signal_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON trading_signals(timestamp DESC)')
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database tables initialized (with KIRA support)")
        return True
    except Exception as e:
        logger.error(f"❌ DB init error: {e}")
        return False

init_database()

@app.route('/')
def home():
    return jsonify({
        "service": "TradingView Proxy API",
        "status": "running",
        "version": "3.0 (KIRA Super-Compatible)",
        "webhook_url": "https://tradingview-proxy-h71n.onrender.com/webhook",
        "endpoints": {
            "health": "/health",
            "webhook": "/webhook (POST/GET)",
            "signals": "/signals (GET)",
            "kiria_signals": "/kiria/signals (GET)",
            "delete_all": "/delete_all (DELETE) - очистить все сигналы"
        }
    })

@app.route('/health')
def health():
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM trading_signals")
            trading_count = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM kiria_full_signals")
            kiria_count = cur.fetchone()[0]
            
            cur.close()
            conn.close()
            
            db_status = {
                "trading_signals": trading_count,
                "kiria_full_signals": kiria_count,
                "status": "connected"
            }
        else:
            db_status = {"status": "disconnected"}
            
        return jsonify({
            "status": "healthy",
            "database": db_status,
            "timestamp": datetime.now().isoformat(),
            "version": "3.0"
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

def extract_signal_info(data):
    """Извлекает информацию о сигнале из любых данных"""
    # Безопасное извлечение с значениями по умолчанию
    symbol = str(data.get('symbol') or data.get('ticker') or 'UNKNOWN')[:50]
    
    # Сигнал может быть в разных полях
    signal = str(
        data.get('signal') or 
        data.get('action') or 
        data.get('order') or 
        data.get('alert_type') or 
        'UNKNOWN'
    )[:50]
    
    # Пробуем получить цену разными способами
    price_value = data.get('price') or data.get('close') or data.get('value') or 0
    try:
        price = float(price_value)
    except (ValueError, TypeError):
        price = 0.0
    
    return {
        'symbol': symbol.upper(),
        'signal': signal.upper(),
        'price': price,
        'source': data.get('source', 'unknown')
    }

@app.route('/webhook', methods=['POST', 'GET', 'PUT', 'OPTIONS'])
def webhook():
    """Универсальный вебхук для TradingView - принимает ВСЁ"""
    try:
        data = {}
        content_type = request.content_type or ''
        
        logger.info(f"📨 Получен запрос: {request.method}, Content-Type: {content_type}")
        
        # 🔥 ВАЖНО: Принимаем ЛЮБОЙ формат данных
        
        # 1. JSON (нормальный запрос)
        if request.is_json:
            try:
                data = request.get_json()
                logger.info("✅ Данные получены как JSON")
            except:
                logger.warning("⚠️ Не удалось распарсить JSON")
        
        # 2. Form-data (HTML формы)
        elif 'form-data' in content_type or 'x-www-form-urlencoded' in content_type:
            if request.form:
                data = request.form.to_dict()
                logger.info(f"✅ Данные получены как form-data: {len(data)} полей")
        
        # 3. Raw text/plain (часто TradingView так отправляет)
        elif 'text/plain' in content_type or request.data:
            try:
                raw_text = request.data.decode('utf-8')
                logger.info(f"📝 Raw данные: {raw_text[:200]}...")
                
                # Пробуем разные форматы:
                
                # JSON в тексте
                if raw_text.strip().startswith('{'):
                    try:
                        data = json.loads(raw_text)
                        logger.info("✅ Raw текст распознан как JSON")
                    except json.JSONDecodeError:
                        # Может быть JSON с лишними символами
                        cleaned = raw_text.strip()
                        if cleaned.startswith('"') and cleaned.endswith('"'):
                            cleaned = cleaned[1:-1]
                        try:
                            data = json.loads(cleaned)
                        except:
                            data = {'raw': raw_text}
                
                # URL encoded (symbol=BTC&price=50000)
                elif '=' in raw_text and ('&' in raw_text or '\n' in raw_text):
                    try:
                        # Заменяем переносы строк на &
                        normalized = raw_text.replace('\n', '&').replace('\r', '')
                        parsed = urllib.parse.parse_qs(normalized)
                        data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
                        logger.info("✅ Raw текст распознан как URL encoded")
                    except:
                        data = {'raw': raw_text}
                
                # Просто текст
                else:
                    data = {'message': raw_text}
                    
            except Exception as e:
                logger.error(f"❌ Ошибка обработки raw данных: {e}")
                data = {'error': str(e), 'raw_bytes': len(request.data)}
        
        # 4. GET параметры (для тестирования через браузер)
        elif request.method == 'GET':
            data = request.args.to_dict()
            logger.info(f"🔗 GET параметры: {data}")
        
        # 5. Если ничего не получили
        if not data:
            logger.warning("⚠️ Пустой запрос без данных")
            return jsonify({
                "status": "warning",
                "message": "Empty request received",
                "tip": "Send JSON with symbol, signal, price"
            }), 200
        
        # 🔍 ИЗВЛЕКАЕМ ДАННЫЕ СИГНАЛА
        signal_info = extract_signal_info(data)
        symbol = signal_info['symbol']
        signal = signal_info['signal']
        price = signal_info['price']
        
        # Если нет обязательных данных
        if symbol == 'UNKNOWN' or signal == 'UNKNOWN' or price == 0:
            logger.warning(f"⚠️ Неполные данные: {symbol} {signal} ${price}")
        
        # 📊 СОХРАНЕНИЕ В БАЗУ
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        
        try:
            # Сохраняем в основную таблицу
            cur.execute('''
                INSERT INTO trading_signals (symbol, signal, price, source)
                VALUES (%s, %s, %s, %s)
                RETURNING id, timestamp
            ''', (symbol, signal, price, content_type))
            
            signal_id, timestamp = cur.fetchone()
            
            # Сохраняем ВСЕ исходные данные в KIRA таблицу
            cur.execute('''
                INSERT INTO kiria_full_signals (signal_id, full_data)
                VALUES (%s, %s)
            ''', (signal_id, json.dumps(data)))
            
            conn.commit()
            
            # 📝 ЛОГИРОВАНИЕ
            logger.info(f"✅ Сигнал сохранен: {symbol} {signal} ${price:.2f} (ID: {signal_id})")
            
            # Проверяем наличие KIRA данных
            kira_keys = ['monitoring_minutes', 'delta_15min', 'bull_percent', 'dominance']
            has_kira = any(key in data for key in kira_keys)
            
            if has_kira:
                kira_info = {k: data.get(k) for k in kira_keys if k in data}
                logger.info(f"   📊 KIRA данные: {kira_info}")
            
        except Exception as db_error:
            conn.rollback()
            logger.error(f"❌ Ошибка базы данных: {db_error}")
            return jsonify({"error": f"Database error: {db_error}"}), 500
        finally:
            cur.close()
            conn.close()
        
        # ✅ УСПЕШНЫЙ ОТВЕТ
        return jsonify({
            "status": "success",
            "message": "Signal received and saved",
            "signal_id": signal_id,
            "data": {
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "timestamp": timestamp.isoformat() if timestamp else None,
                "format_received": content_type,
                "has_kira_data": has_kira
            }
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка вебхука: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "error": str(e),
            "tip": "Check your data format. Send JSON like: {\"symbol\":\"BTC\",\"signal\":\"BUY\",\"price\":50000}"
        }), 500

# 🗑️ Очистка всех сигналов (для тестирования)
@app.route('/delete_all', methods=['DELETE', 'POST'])
def delete_all_signals():
    """Удаляет все сигналы (осторожно!)"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        cur = conn.cursor()
        
        # Удаляем в правильном порядке из-за foreign key
        cur.execute("DELETE FROM kiria_full_signals")
        cur.execute("DELETE FROM trading_signals")
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.warning("⚠️ Все сигналы удалены!")
        
        return jsonify({
            "status": "success",
            "message": "All signals deleted",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 📊 Получение всех сигналов
@app.route('/signals')
def get_signals():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        limit = min(int(request.args.get('limit', 50)), 1000)
        offset = int(request.args.get('offset', 0))
        
        cur = conn.cursor()
        cur.execute('''
            SELECT id, symbol, signal, price, timestamp, source 
            FROM trading_signals 
            ORDER BY timestamp DESC 
            LIMIT %s OFFSET %s
        ''', (limit, offset))
        
        signals = []
        for row in cur.fetchall():
            signals.append({
                "id": row[0],
                "symbol": row[1],
                "signal": row[2],
                "price": float(row[3]),
                "timestamp": row[4].isoformat() if row[4] else None,
                "source": row[5]
            })
        
        cur.execute("SELECT COUNT(*) FROM trading_signals")
        total = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "total": total,
            "signals": signals
        })
    except Exception as e:
        logger.error(f"Error in get_signals: {e}")
        return jsonify({"error": str(e)}), 500

# 📈 Получение KIRA сигналов
@app.route('/kiria/signals')
def get_kiria_signals():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database not connected"}), 500
        
        limit = min(int(request.args.get('limit', 50)), 1000)
        
        cur = conn.cursor()
        
        # Проверяем существование таблицы
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kiria_full_signals')")
        if not cur.fetchone()[0]:
            cur.close()
            conn.close()
            return jsonify({
                "status": "success",
                "message": "KIRA table not created yet",
                "signals": []
            })
        
        cur.execute('''
            SELECT 
                ts.id,
                ts.symbol,
                ts.signal,
                ts.price,
                ts.timestamp,
                kfs.full_data
            FROM trading_signals ts
            LEFT JOIN kiria_full_signals kfs ON ts.id = kfs.signal_id
            WHERE kfs.full_data IS NOT NULL
            ORDER BY ts.timestamp DESC
            LIMIT %s
        ''', (limit,))
        
        signals = []
        for row in cur.fetchall():
            try:
                full_data = json.loads(row[5]) if isinstance(row[5], str) else row[5]
            except:
                full_data = {}
            
            # Извлекаем KIRA поля
            kira_data = {
                "monitoring_minutes": full_data.get('monitoring_minutes', 0),
                "delta_15min": full_data.get('delta_15min', 0),
                "total_delta_90min": full_data.get('total_delta_90min', 0),
                "bull_percent": full_data.get('bull_percent', 50),
                "dominance": full_data.get('dominance', 'NEUTRAL'),
                "channel_data": full_data.get('channel_data', {})
            }
            
            signals.append({
                "id": row[0],
                "symbol": row[1],
                "signal": row[2],
                "price": float(row[3]),
                "timestamp": row[4].isoformat() if row[4] else None,
                "kira_data": kira_data,
                "full_data": full_data
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(signals),
            "signals": signals
        })
    except Exception as e:
        logger.error(f"Error in get_kiria_signals: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 KIRA TradingView Proxy запущен на порту {port}")
    logger.info(f"🌐 Доступ по URL: https://tradingview-proxy-h71n.onrender.com")
    logger.info(f"✅ Вебхук URL: https://tradingview-proxy-h71n.onrender.com/webhook")
    logger.info(f"📊 KIRA эндпоинты:")
    logger.info(f"   - GET /signals - все сигналы")
    logger.info(f"   - GET /kiria/signals - KIRA сигналы")
    logger.info(f"   - DELETE /delete_all - очистить все (для тестов)")
    app.run(host='0.0.0.0', port=port, debug=False)
