from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
import sqlite3, os, uuid, json, asyncio
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).with_name("turf_booker.db")))
LOCK_TTL = int(os.getenv("LOCK_TTL_SECONDS", "180"))

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS venues (id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS courts (id INTEGER PRIMARY KEY, venue_id INTEGER NOT NULL REFERENCES venues(id), name TEXT NOT NULL, sport TEXT NOT NULL, surface TEXT NOT NULL, hourly_base INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS slot_locks (id INTEGER PRIMARY KEY AUTOINCREMENT, court_id INTEGER NOT NULL REFERENCES courts(id), slot_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, token TEXT NOT NULL UNIQUE, user_name TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(court_id, slot_date, start_time));
CREATE TABLE IF NOT EXISTS bookings (id TEXT PRIMARY KEY, court_id INTEGER NOT NULL REFERENCES courts(id), slot_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, customer_name TEXT NOT NULL, phone TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'confirmed', created_at TEXT NOT NULL, UNIQUE(court_id, slot_date, start_time));
CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id), amount INTEGER NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_locks_expiry ON slot_locks(expires_at);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(slot_date, court_id);
'''

def connect():
    c = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    c.execute('PRAGMA busy_timeout = 15000')
    return c

def init_db():
    c = connect(); c.executescript(SCHEMA)
    if c.execute('SELECT COUNT(*) FROM venues').fetchone()[0] == 0:
        c.execute('INSERT INTO venues(name,address,description) VALUES (?,?,?)', ('Arena Sports Hub','Peelamedu, Coimbatore','Premium indoor courts with changing rooms and parking.'))
        c.execute('INSERT INTO venues(name,address,description) VALUES (?,?,?)', ('Green Turf Sports','Saravanampatti, Coimbatore','Outdoor turf with floodlights for evening games.'))
        v1 = c.execute('SELECT id FROM venues WHERE name=?',('Arena Sports Hub',)).fetchone()['id']
        v2 = c.execute('SELECT id FROM venues WHERE name=?',('Green Turf Sports',)).fetchone()['id']
        for v, rows in [(v1,[('Badminton Court 1','Badminton','Synthetic',350),('Badminton Court 2','Badminton','Wooden',400),('Futsal Turf','Football','Artificial turf',900)]),(v2,[('5-a-side Turf','Football','Artificial turf',800),('Multi-sport Court','Badminton','Acrylic',300)])]:
            for name,sport,surface,price in rows: c.execute('INSERT INTO courts(venue_id,name,sport,surface,hourly_base) VALUES(?,?,?,?,?)',(v,name,sport,surface,price))
    c.close()

class Hub:
    def __init__(self): self.clients=set(); self.lock=asyncio.Lock()
    async def connect(self, ws):
        await ws.accept()
        async with self.lock: self.clients.add(ws)
    async def disconnect(self, ws):
        async with self.lock: self.clients.discard(ws)
    async def broadcast(self, payload):
        async with self.lock: clients=list(self.clients)
        dead=[]
        for ws in clients:
            try: await ws.send_json(payload)
            except Exception: dead.append(ws)
        for ws in dead: await self.disconnect(ws)
hub=Hub()

@asynccontextmanager
async def lifespan(app):
    init_db(); yield
app=FastAPI(title='Turf & Badminton Court Slot Booker API',version='1.0.0',lifespan=lifespan)
origins=os.getenv('FRONTEND_ORIGIN','http://localhost:5173').split(',')
app.add_middleware(CORSMiddleware,allow_origins=origins+['http://127.0.0.1:5173'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

class LockRequest(BaseModel): court_id:int; slot_date:date; start_time:str; user_name:str=Field(min_length=2,max_length=80)
class BookingRequest(BaseModel): lock_token:str; customer_name:str=Field(min_length=2,max_length=80); phone:str=Field(min_length=8,max_length=20); payment_method:str='demo'

def expire_locks(c): c.execute('DELETE FROM slot_locks WHERE expires_at <= ?', (datetime.utcnow().isoformat(),))
def price_for(base, day, hour):
    d=date.fromisoformat(day); peak=(18 <= hour < 22); weekend=d.weekday()>=5
    multiplier=1.0 + (0.35 if peak else 0) + (0.15 if weekend else 0)
    return int(round(base*multiplier/10)*10), ('Peak + weekend' if peak and weekend else 'Peak hour' if peak else 'Weekend' if weekend else 'Standard')

def slot_rows(c,court_id,day):
    court=c.execute('SELECT * FROM courts WHERE id=? AND active=1',(court_id,)).fetchone()
    if not court: raise HTTPException(404,'Court not found')
    expire_locks(c)
    booked={r['start_time']:r for r in c.execute('SELECT * FROM bookings WHERE court_id=? AND slot_date=? AND status="confirmed"',(court_id,day)).fetchall()}
    locked={r['start_time']:r for r in c.execute('SELECT * FROM slot_locks WHERE court_id=? AND slot_date=?',(court_id,day)).fetchall()}
    slots=[]
    for h in range(6,23):
        start=f'{h:02d}:00'; end=f'{h+1:02d}:00'; amount,label=price_for(court['hourly_base'],day,h)
        if start in booked: status='booked'
        elif start in locked: status='locked'
        else: status='available'
        slots.append({'start_time':start,'end_time':end,'status':status,'price':amount,'tariff':label,'expires_at':locked[start]['expires_at'] if start in locked else None})
    return slots

@app.get('/')
def root(): return {'message':'Turf Slot Booker API is running','docs':'/docs'}
@app.get('/api/health')
def health(): return {'status':'ok','database':'sqlite','lock_ttl_seconds':LOCK_TTL}
@app.get('/api/venues')
def venues():
    c=connect(); rows=c.execute('SELECT * FROM venues WHERE active=1 ORDER BY id').fetchall(); out=[]
    for r in rows: out.append(dict(r))
    c.close(); return out
@app.get('/api/courts')
def courts(venue_id:Optional[int]=None):
    c=connect(); q='SELECT courts.*,venues.name AS venue_name,venues.address FROM courts JOIN venues ON venues.id=courts.venue_id WHERE courts.active=1'; args=[]
    if venue_id: q+=' AND venue_id=?'; args.append(venue_id)
    rows=c.execute(q+' ORDER BY courts.id',args).fetchall(); c.close(); return [dict(r) for r in rows]
@app.get('/api/slots')
def slots(court_id:int, slot_date:date):
    c=connect(); out=slot_rows(c,court_id,slot_date.isoformat()); c.close(); return {'court_id':court_id,'slot_date':slot_date.isoformat(),'slots':out,'lock_ttl_seconds':LOCK_TTL}
@app.post('/api/locks')
async def acquire_lock(req:LockRequest):
    try: hour=int(req.start_time.split(':')[0]); assert req.start_time==f'{hour:02d}:00' and 6<=hour<23
    except: raise HTTPException(400,'Choose a valid one-hour slot between 06:00 and 22:00')
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE'); expire_locks(c)
        court=c.execute('SELECT id FROM courts WHERE id=? AND active=1',(req.court_id,)).fetchone()
        if not court: raise HTTPException(404,'Court not found')
        day=req.slot_date.isoformat()
        if c.execute('SELECT 1 FROM bookings WHERE court_id=? AND slot_date=? AND start_time=? AND status="confirmed"',(req.court_id,day,req.start_time)).fetchone(): raise HTTPException(409,'This slot has already been booked')
        if c.execute('SELECT 1 FROM slot_locks WHERE court_id=? AND slot_date=? AND start_time=?',(req.court_id,day,req.start_time)).fetchone(): raise HTTPException(409,'This slot is being held by another player. Please choose another slot.')
        token=uuid.uuid4().hex; expiry=(datetime.utcnow()+timedelta(seconds=LOCK_TTL)).isoformat()
        c.execute('INSERT INTO slot_locks(court_id,slot_date,start_time,end_time,token,user_name,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)',(req.court_id,day,req.start_time,f'{hour+1:02d}:00',token,req.user_name,expiry,datetime.utcnow().isoformat()))
        c.execute('COMMIT')
        await hub.broadcast({'type':'slot_changed','court_id':req.court_id,'slot_date':day,'start_time':req.start_time})
        return {'lock_token':token,'expires_at':expiry,'ttl_seconds':LOCK_TTL}
    except HTTPException:
        c.execute('ROLLBACK'); raise
    except sqlite3.IntegrityError:
        c.execute('ROLLBACK'); raise HTTPException(409,'Slot is no longer available')
    finally: c.close()
@app.delete('/api/locks/{token}')
async def release_lock(token:str):
    c=connect(); row=c.execute('SELECT court_id,slot_date,start_time FROM slot_locks WHERE token=?',(token,)).fetchone(); c.execute('DELETE FROM slot_locks WHERE token=?',(token,)); c.close()
    if row: await hub.broadcast({'type':'slot_changed','court_id':row['court_id'],'slot_date':row['slot_date'],'start_time':row['start_time']})
    return {'released':True}
@app.post('/api/bookings')
async def create_booking(req:BookingRequest):
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE'); expire_locks(c)
        lock=c.execute('SELECT * FROM slot_locks WHERE token=?',(req.lock_token,)).fetchone()
        if not lock: raise HTTPException(409,'Your slot hold expired. Please select the slot again.')
        court=c.execute('SELECT * FROM courts WHERE id=?',(lock['court_id'],)).fetchone()
        amount,_=price_for(court['hourly_base'],lock['slot_date'],int(lock['start_time'][:2]))
        bid='BK-'+uuid.uuid4().hex[:8].upper(); now=datetime.utcnow().isoformat()
        c.execute('INSERT INTO bookings(id,court_id,slot_date,start_time,end_time,customer_name,phone,amount,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(bid,lock['court_id'],lock['slot_date'],lock['start_time'],lock['end_time'],req.customer_name,req.phone,amount,'confirmed',now))
        pid='PAY-'+uuid.uuid4().hex[:8].upper()
        c.execute('INSERT INTO payments(id,booking_id,amount,method,status,created_at) VALUES(?,?,?,?,?,?)',(pid,bid,amount,req.payment_method,'paid_demo',now))
        court_id,day,start=lock['court_id'],lock['slot_date'],lock['start_time']; c.execute('DELETE FROM slot_locks WHERE token=?',(req.lock_token,)); c.execute('COMMIT')
        await hub.broadcast({'type':'booking_created','court_id':court_id,'slot_date':day,'start_time':start})
        return {'booking_id':bid,'payment_id':pid,'amount':amount,'status':'confirmed','message':'Booking confirmed. Demo payment recorded.'}
    except HTTPException:
        c.execute('ROLLBACK'); raise
    except sqlite3.IntegrityError:
        c.execute('ROLLBACK'); raise HTTPException(409,'This slot was just booked by someone else')
    finally: c.close()
@app.get('/api/bookings')
def list_bookings():
    c=connect(); rows=c.execute('SELECT b.*,co.name AS court_name,v.name AS venue_name,p.id AS payment_id,p.status AS payment_status FROM bookings b JOIN courts co ON co.id=b.court_id JOIN venues v ON v.id=co.venue_id LEFT JOIN payments p ON p.booking_id=b.id ORDER BY b.created_at DESC LIMIT 100').fetchall(); c.close(); return [dict(r) for r in rows]
@app.websocket('/ws')
async def websocket_endpoint(ws:WebSocket):
    await hub.connect(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: await hub.disconnect(ws)
