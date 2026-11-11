package nl.mediaguard.app

import android.annotation.SuppressLint
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import nl.mediaguard.app.data.tr

/** Wat op deze telefoon meldingen kan tegenhouden, met de knop die het oplost. */
data class PhoneState(val notificationsAllowed: Boolean = true, val batteryExempt: Boolean = true) {
    /** Korte waarschuwing voor Overzicht; null als alles goed staat. */
    fun warning(ntfyOn: Boolean): String? = when {
        !notificationsAllowed -> tr("Meldingen staan uit voor MediaGuard op deze telefoon", "Notifications are off for MediaGuard on this phone")
        !batteryExempt && ntfyOn -> tr("Batterijbeperking kan meldingen uren vertragen", "Battery restriction can delay notifications for hours")
        else -> null
    }
}

object PhoneChecks {
    fun read(context: Context): PhoneState {
        val nm = NotificationManagerCompat.from(context)
        val channelOn = runCatching {
            context.getSystemService(NotificationManager::class.java)
                .getNotificationChannel(MediaGuardApp.CHANNEL_ALERTS)?.importance != NotificationManager.IMPORTANCE_NONE
        }.getOrDefault(true)
        val battery = runCatching {
            context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName)
        }.getOrDefault(true)
        return PhoneState(nm.areNotificationsEnabled() && channelOn, battery)
    }

    /** Zit de telefoon op een netwerk waarmee een thuisadres te bereiken kan zijn (wifi, ethernet of VPN)?
     *  Onbekend = ja, zodat een echte storing nooit stil blijft. */
    fun onHomeCapableNetwork(context: Context): Boolean = runCatching {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return@runCatching false
        caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
    }.getOrDefault(true)

    fun openNotificationSettings(context: Context) {
        val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }
    }

    /** Vraagt de batterij-uitzondering rechtstreeks (de app staat niet in de Play Store); anders de lijst. */
    @SuppressLint("BatteryLife")
    fun requestBatteryExemption(context: Context) {
        val direct = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(direct) }.isFailure) {
            runCatching {
                context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
    }
}
