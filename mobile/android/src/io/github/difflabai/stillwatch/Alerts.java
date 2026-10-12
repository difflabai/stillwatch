package io.github.difflabai.stillwatch;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import org.json.JSONObject;

final class Alerts {
    static void show(Context context,Store store,String tag,JSONObject state) throws Exception {
        NotificationManager manager=context.getSystemService(NotificationManager.class);
        boolean severe="severe".equals(state.getString("severity"));
        String channel=severe?"severe":"attention";
        NotificationChannel definition=new NotificationChannel(channel,severe?"Severe · Drop everything":"Needs your attention",NotificationManager.IMPORTANCE_HIGH);
        definition.enableVibration(true);definition.setVibrationPattern(severe?new long[]{0,500,200,500,200,1000}:new long[]{0,180});
        manager.createNotificationChannel(definition);
        store.saveAlert(tag,state.getJSONArray("events"));
        Intent action=new Intent(context,DismissReceiver.class).setAction("io.github.difflabai.stillwatch.DISMISS")
            .setData(Uri.parse("stillwatch://dismiss/"+Uri.encode(tag))).putExtra("tag",tag);
        PendingIntent dismiss=PendingIntent.getBroadcast(context,0,action,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open=PendingIntent.getActivity(context,0,new Intent(context,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification notification=new Notification.Builder(context,channel).setSmallIcon(R.drawable.icon)
            .setContentTitle((severe?"DROP EVERYTHING · ":"NEEDS YOU · ")+state.getString("title"))
            .setContentText(state.getString("app")+" · "+state.getString("body"))
            .setStyle(new Notification.BigTextStyle().bigText(state.getString("body")))
            .setColor(severe?0xfff66151:0xfff6c556).setOnlyAlertOnce(true).setAutoCancel(false)
            .setContentIntent(open).setDeleteIntent(dismiss)
            .addAction(new Notification.Action.Builder(null,"Mark handled",dismiss).build())
            // Leave localOnly false: the approved alert may bridge to Pixel Watch.
            .setLocalOnly(false).setVisibility(Notification.VISIBILITY_PRIVATE).build();
        manager.notify(tag,0,notification);
    }
    static void cancel(Context context,Store store,String tag) {
        context.getSystemService(NotificationManager.class).cancel(tag,0);store.removeAlert(tag);
    }
}
