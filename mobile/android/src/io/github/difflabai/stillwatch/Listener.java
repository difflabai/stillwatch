package io.github.difflabai.stillwatch;

import android.app.Notification;
import android.content.pm.ApplicationInfo;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONObject;

public class Listener extends NotificationListenerService {
    static volatile Listener running;
    private ScheduledExecutorService worker;
    private Store store;
    private Settings settings;
    @Override public void onListenerConnected() {
        running=this;store=new Store(this);settings=new Settings(this);
        if(worker!=null)worker.shutdownNow();
        worker=Executors.newSingleThreadScheduledExecutor();
        if(settings.ready()) {
            StatusBarNotification[] active=getActiveNotifications();
            if(active!=null) for(StatusBarNotification notification:active) capture(notification);
        }
        worker.scheduleWithFixedDelay(()->sync(),0,5,TimeUnit.SECONDS);
    }
    @Override public void onListenerDisconnected() {
        running=null;
        if(worker!=null) worker.shutdownNow();
    }
    @Override public void onDestroy() {
        running=null;if(worker!=null)worker.shutdownNow();super.onDestroy();
    }
    static void kick(){Listener current=running;if(current!=null && current.worker!=null)current.worker.execute(()->current.sync());}
    static void imported(){Listener current=running;if(current!=null){StatusBarNotification[] active=current.getActiveNotifications();if(active!=null)for(StatusBarNotification item:active)current.capture(item);kick();}}
    static String clip(CharSequence value,int max){String text=value==null?"":value.toString();return text.length()<=max?text:text.substring(0,max);}
    private void capture(StatusBarNotification notification) {
        if(!settings.ready() || getPackageName().equals(notification.getPackageName()))return;
        try {
            Bundle extras=notification.getNotification().extras;
            String title=clip(extras.getCharSequence(Notification.EXTRA_TITLE),10000);
            CharSequence big=extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
            String body=clip(big!=null?big:extras.getCharSequence(Notification.EXTRA_TEXT),10000);
            String app=notification.getPackageName();
            try {
                ApplicationInfo info=getPackageManager().getApplicationInfo(app,0);
                app=clip(getPackageManager().getApplicationLabel(info),200);
            } catch(Exception ignored){}
            // Only the user-selected app allowlist opts new content into cloud triage.
            store.posted(notification.getKey(),app,title,body,notification.getPostTime(),notification.isClearable(),settings.cloudAllowed(notification.getPackageName()),settings);
            kick();
        }catch(Exception error){settings.failed(error);}
    }
    @Override public void onNotificationPosted(StatusBarNotification notification){if(store!=null)capture(notification);}
    @Override public void onNotificationRemoved(StatusBarNotification notification,RankingMap ranking,int reason) {
        if(store==null || getPackageName().equals(notification.getPackageName()))return;
        // Automatic expiry, replacement, app shutdown and listener cancellation are not user handling.
        if(reason==REASON_CANCEL || reason==REASON_CANCEL_ALL || reason==REASON_CLICK) {
            try{store.nativeDismissed(notification.getKey());kick();}catch(Exception error){settings.failed(error);}
        }
    }
    private void sync() {
        if(!settings.ready())return;
        try {
            JSONObject request=new JSONObject().put("version",1).put("device",settings.device()).put("after",settings.cursor()).put("operations",store.outbox());
            JSONObject response=Transport.exchange(settings,request);
            if(response.getInt("version")!=1 || response.getLong("cursor")<settings.cursor())throw new IllegalArgumentException("Invalid bridge cursor");
            JSONArray states=response.getJSONArray("states");
            String authority=response.getString("authority");
            for(int i=0;i<states.length();i++) {
                JSONObject state=states.getJSONObject(i);
                String tag="stillwatch:"+authority+":"+state.getLong("id");
                JSONArray events=state.getJSONArray("events");
                if(state.getBoolean("handled")) {
                    Alerts.cancel(this,store,tag);
                    JSONArray nativeKeys=store.nativeKeys(events);
                    for(int j=0;j<nativeKeys.length();j++)cancelNotification(nativeKeys.getString(j));
                } else if("immediate".equals(state.getString("decision"))) {
                    Alerts.show(this,store,tag,state);
                } else {Alerts.cancel(this,store,tag);}
            }
            JSONArray removed=store.alertsForEvents(response.getJSONArray("removed_events"));
            for(int i=0;i<removed.length();i++)Alerts.cancel(this,store,removed.getString(i));
            store.acknowledge(response.getJSONArray("ack"));
            settings.succeeded(response.getLong("cursor"));
        }catch(Exception error){settings.failed(error);}
    }
}
