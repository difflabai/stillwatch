package io.github.difflabai.stillwatch;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class DismissReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent) {
        if(!"io.github.difflabai.stillwatch.DISMISS".equals(intent.getAction()))return;
        String tag=intent.getStringExtra("tag");
        if(tag==null)return;
        Store store=new Store(context);
        try {store.dismissAlert(tag);context.getSystemService(android.app.NotificationManager.class).cancel(tag,0);Listener.kick();}
        catch(Exception error){new Settings(context).failed(error);}
        finally{store.close();}
    }
}
