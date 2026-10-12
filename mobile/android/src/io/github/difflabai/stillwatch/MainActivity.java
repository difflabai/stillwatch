package io.github.difflabai.stillwatch;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private io.github.difflabai.stillwatch.Settings configuration;
    private TextView status;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        configuration=new io.github.difflabai.stillwatch.Settings(this);
        ScrollView scroll=new ScrollView(this);scroll.setBackgroundColor(0xff171b22);
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);
        int padding=(int)(20*getResources().getDisplayMetrics().density);
        root.setPadding(padding,padding,padding,padding);scroll.addView(root);setContentView(scroll);
        root.setOnApplyWindowInsetsListener((view,insets)->{view.setPadding(padding,padding+insets.getSystemWindowInsetTop(),padding,padding+insets.getSystemWindowInsetBottom());return insets;});
        label(root,"Stillwatch",28,true);
        label(root,"Protect your attention. Direct it to what matters.",16,false);
        status=label(root,"",14,false);status.setTypeface(Typeface.MONOSPACE);
        button(root,"1 · Import private pairing file",()->{
            Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/json").addCategory(Intent.CATEGORY_OPENABLE);
            intent.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"application/json","text/plain","application/octet-stream"});
            startActivityForResult(intent,1);
        });
        button(root,"2 · Choose apps for AI",()->chooseApps());
        label(root,"Only apps you select send new notification text to NanoGPT through your server. Everything else stays in your private shared history. No model key is stored on this phone.",14,false);
        button(root,"3 · Enable notification capture",()->startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        button(root,"4 · Allow Stillwatch alerts",()->{
            if(Build.VERSION.SDK_INT>=33)requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},2);
            else startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE,getPackageName()));
        });
        button(root,"Battery settings for timely sync",()->startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)));
        button(root,"Refresh status",()->{Listener.kick();refresh();});
        label(root,"Pixel Watch: mirror Stillwatch from phone apps, and turn off direct mirroring for the original apps you want filtered. Check watch-installed apps separately. Keep original phone notifications enabled; set their channels silent if you want a quiet phone.",14,false);
        label(root,"Dismiss or tap Mark handled on an alert to handle it on your server, laptop, phone and bridged watch. Phone calls, alarms, protected content and other profiles have platform limits. This alpha needs a real phone/watch check before replacing their existing alert paths.",14,false);
        refresh();
    }
    private TextView label(LinearLayout root,String text,int size,boolean bold) {
        TextView view=new TextView(this);view.setText(text);view.setTextSize(size);view.setTextColor(0xffe9edf3);
        view.setPadding(0,8,0,16);if(bold)view.setTypeface(Typeface.DEFAULT,Typeface.BOLD);root.addView(view);return view;
    }
    private void button(LinearLayout root,String text,Runnable action) {
        Button button=new Button(this);button.setText(text);button.setAllCaps(false);button.setOnClickListener(view->action.run());root.addView(button);
    }
    private void refresh() {
        String text=configuration.ready()?"Paired with your server\n":"Not paired yet\n";
        text+="Notification listener: "+(Listener.running!=null?"connected":"enable in Settings")+"\n";
        text+="AI apps selected: "+configuration.cloudApps().size()+"\n";
        long last=configuration.preferences.getLong("last_sync",0);
        text+="Last sync: "+(last==0?"not connected":((System.currentTimeMillis()-last)/1000)+" seconds ago");
        String error=configuration.preferences.getString("error","");if(!error.isEmpty())text+="\nConnection status: "+error;
        status.setText(text);
    }
    @Override public void onResume(){super.onResume();if(configuration!=null)refresh();}
    @Override public void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(request!=1 || result!=RESULT_OK || data==null || data.getData()==null)return;
        try(InputStream input=getContentResolver().openInputStream(data.getData())) {
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] buffer=new byte[1024];int read;
            while((read=input.read(buffer))!=-1){bytes.write(buffer,0,read);if(bytes.size()>8192)throw new IllegalArgumentException("Pairing file too large");}
            configuration.importPairing(new JSONObject(bytes.toString("UTF-8")));Listener.imported();refresh();
        }catch(Exception error){new AlertDialog.Builder(this).setTitle("Pairing could not be imported").setMessage(error.getClass().getSimpleName()+". Use the private pairing file from your Stillwatch server.").setPositiveButton("OK",null).show();}
    }
    private void chooseApps() {
        List<ApplicationInfo> apps=new ArrayList<>(getPackageManager().getInstalledApplications(0));
        apps.removeIf(info->getPackageName().equals(info.packageName));
        apps.sort(Comparator.comparing(info->getPackageManager().getApplicationLabel(info).toString(),String.CASE_INSENSITIVE_ORDER));
        String[] names=new String[apps.size()];boolean[] checked=new boolean[apps.size()];Set<String> selected=configuration.cloudApps();
        for(int i=0;i<apps.size();i++){names[i]=getPackageManager().getApplicationLabel(apps.get(i))+"\n"+apps.get(i).packageName;checked[i]=selected.contains(apps.get(i).packageName);}
        new AlertDialog.Builder(this).setTitle("Allow cloud triage for these apps")
            .setMultiChoiceItems(names,checked,(dialog,index,on)->{if(on)selected.add(apps.get(index).packageName);else selected.remove(apps.get(index).packageName);})
            .setPositiveButton("Save",(dialog,which)->{configuration.cloudApps(selected);refresh();})
            .setNegativeButton("Cancel",null).show();
    }
}
