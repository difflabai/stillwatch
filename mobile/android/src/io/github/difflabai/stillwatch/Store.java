package io.github.difflabai.stillwatch;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.security.MessageDigest;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

final class Store extends SQLiteOpenHelper {
    Store(Context context) { super(context,"events.sqlite3",null,1); }
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT UNIQUE, data TEXT NOT NULL)");
        db.execSQL("CREATE TABLE native_events (native_key TEXT PRIMARY KEY,event TEXT NOT NULL,digest TEXT NOT NULL,posted INTEGER NOT NULL,clearable INTEGER NOT NULL)");
        db.execSQL("CREATE TABLE alerts (tag TEXT PRIMARY KEY, events TEXT NOT NULL)");
    }
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion) {}
    static String digest(String text) throws Exception {
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest(text.getBytes("UTF-8"));
        StringBuilder out=new StringBuilder(); for (byte b:bytes) out.append(String.format(java.util.Locale.ROOT,"%02x",b&255)); return out.toString();
    }
    void enqueue(String kind,JSONObject payload) throws Exception {
        JSONObject item=new JSONObject().put("operation",UUID.randomUUID().toString()).put("kind",kind).put("payload",payload);
        getWritableDatabase().execSQL("INSERT INTO outbox(operation,data) VALUES (?,?)",new Object[]{item.getString("operation"),item.toString()});
    }
    synchronized void posted(String nativeKey,String app,String title,String body,long posted,boolean clearable,boolean cloud,Settings settings) throws Exception {
        SQLiteDatabase db=getWritableDatabase(); String digest=digest(app+"\0"+title+"\0"+body);
        try (Cursor cursor=db.rawQuery("SELECT digest,posted FROM native_events WHERE native_key=?",new String[]{nativeKey})) {
            if (cursor.moveToFirst() && digest.equals(cursor.getString(0)) && posted==cursor.getLong(1)) return;
        }
        String event=UUID.randomUUID().toString();
        JSONObject payload=new JSONObject().put("event",event).put("device",settings.device()).put("label",settings.label())
            .put("received",posted/1000.0).put("app",app).put("title",title).put("body",body).put("cloud_allowed",cloud);
        db.beginTransaction();
        try {
            enqueue("event",payload);
            db.execSQL("INSERT OR REPLACE INTO native_events VALUES (?,?,?,?,?)",new Object[]{nativeKey,event,digest,posted,clearable?1:0});
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }
    synchronized void nativeDismissed(String nativeKey) throws Exception {
        SQLiteDatabase db=getWritableDatabase();
        try (Cursor cursor=db.rawQuery("SELECT event FROM native_events WHERE native_key=?",new String[]{nativeKey})) {
            if (cursor.moveToFirst()) enqueue("dismiss",new JSONObject().put("events",new JSONArray().put(cursor.getString(0))));
        }
        db.execSQL("DELETE FROM native_events WHERE native_key=?",new Object[]{nativeKey});
    }
    JSONArray outbox() throws Exception {
        JSONArray result=new JSONArray();int bytes=0;
        try (Cursor cursor=getReadableDatabase().rawQuery("SELECT data FROM outbox ORDER BY seq LIMIT 64",null)) {
            while (cursor.moveToNext()) {
                String data=cursor.getString(0);int size=data.getBytes("UTF-8").length;
                if(bytes+size>1500000)break;
                result.put(new JSONObject(data));bytes+=size;
            }
        }
        return result;
    }
    void acknowledge(JSONArray ids) throws Exception {
        for (int i=0;i<ids.length();i++) getWritableDatabase().execSQL("DELETE FROM outbox WHERE operation=?",new Object[]{ids.getString(i)});
    }
    void saveAlert(String tag,JSONArray events) { getWritableDatabase().execSQL("INSERT OR REPLACE INTO alerts VALUES (?,?)",new Object[]{tag,events.toString()}); }
    void dismissAlert(String tag) throws Exception {
        try (Cursor cursor=getReadableDatabase().rawQuery("SELECT events FROM alerts WHERE tag=?",new String[]{tag})) {
            if (cursor.moveToFirst()) enqueue("dismiss",new JSONObject().put("events",new JSONArray(cursor.getString(0))));
        }
    }
    void removeAlert(String tag) { getWritableDatabase().execSQL("DELETE FROM alerts WHERE tag=?",new Object[]{tag}); }
    JSONArray nativeKeys(JSONArray events) throws Exception {
        JSONArray keys=new JSONArray();
        for (int i=0;i<events.length();i++) {
            try (Cursor cursor=getReadableDatabase().rawQuery("SELECT native_key FROM native_events WHERE event=? AND clearable=1",new String[]{events.getString(i)})) {
                while(cursor.moveToNext()) keys.put(cursor.getString(0));
            }
        }
        return keys;
    }
    JSONArray alertsForEvents(JSONArray events) throws Exception {
        JSONArray tags=new JSONArray();
        try (Cursor cursor=getReadableDatabase().rawQuery("SELECT tag,events FROM alerts",null)) {
            while(cursor.moveToNext()) {
                JSONArray aliases=new JSONArray(cursor.getString(1)); boolean match=false;
                for(int i=0;i<aliases.length();i++) for(int j=0;j<events.length();j++) if(aliases.getString(i).equals(events.getString(j))) match=true;
                if(match) tags.put(cursor.getString(0));
            }
        }
        return tags;
    }
    long pending() { try(Cursor c=getReadableDatabase().rawQuery("SELECT count(*) FROM outbox",null)) { c.moveToFirst(); return c.getLong(0); } }
}
