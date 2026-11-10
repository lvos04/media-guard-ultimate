package nl.mediaguard.app.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.KeyStore

/**
 * SharedPreferences die een beschadigde versleutelde opslag overleven.
 *
 * Na een systeemupdate, een herstelde back-up of het overzetten naar een nieuwe telefoon past de
 * sleutel in de Android Keystore soms niet meer bij het opgeslagen bestand. EncryptedSharedPreferences
 * gooit dan bij openen of lezen een fout en de app crashte bij elke start. Nu: opslag wissen, opnieuw
 * aanmaken en [recovered] zetten, zodat de app vraagt om opnieuw te koppelen. Lukt zelfs een verse
 * versleutelde opslag niet, dan valt hij terug op [InMemoryPrefs] (niets op schijf; [volatileOnly]).
 */
class ResilientPrefs(
    private val open: () -> SharedPreferences,
    private val wipe: () -> Unit,
    private val fallback: () -> SharedPreferences = { InMemoryPrefs() },
) {
    @Volatile var recovered: Boolean = false
        private set
    @Volatile var volatileOnly: Boolean = false
        private set
    private var prefs: SharedPreferences

    init {
        prefs = try { open() } catch (e: Exception) { reset() }
    }

    private fun reset(): SharedPreferences {
        recovered = true
        runCatching { wipe() }
        return try { open() } catch (e: Exception) { volatileOnly = true; fallback() }
    }

    @Synchronized
    fun <T> read(default: T, block: (SharedPreferences) -> T): T =
        try { block(prefs) } catch (e: Exception) { prefs = reset(); default }

    @Synchronized
    fun write(block: SharedPreferences.Editor.() -> Unit) {
        try {
            prefs.edit().apply(block).apply()
        } catch (e: Exception) {
            prefs = reset()
            runCatching { prefs.edit().apply(block).apply() }
        }
    }
}

/** De echte versleutelde opslag (Android Keystore) en hoe je hem volledig wist. */
object SecureStore {
    const val FILE = "mediaguard_secure"

    fun open(context: Context): SharedPreferences {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        val prefs = EncryptedSharedPreferences.create(
            context, FILE, key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
        prefs.all // alles één keer ontsleutelen: een beschadigd bestand valt nu op, niet midden in een scherm
        return prefs
    }

    fun wipe(context: Context) {
        context.deleteSharedPreferences(FILE)
        runCatching {
            KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(MasterKey.DEFAULT_MASTER_KEY_ALIAS)
        }
    }
}

/** SharedPreferences in het geheugen: terugval als versleutelen niet lukt, en handig in tests. */
class InMemoryPrefs : SharedPreferences {
    private val values = linkedMapOf<String, Any?>()
    private val listeners = mutableSetOf<SharedPreferences.OnSharedPreferenceChangeListener>()

    override fun getAll(): MutableMap<String, *> = synchronized(values) { LinkedHashMap(values) }
    override fun getString(key: String?, defValue: String?): String? = synchronized(values) { values[key] as? String ?: defValue }
    @Suppress("UNCHECKED_CAST")
    override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? =
        synchronized(values) { (values[key] as? Set<String>)?.toMutableSet() ?: defValues }
    override fun getInt(key: String?, defValue: Int): Int = synchronized(values) { values[key] as? Int ?: defValue }
    override fun getLong(key: String?, defValue: Long): Long = synchronized(values) { values[key] as? Long ?: defValue }
    override fun getFloat(key: String?, defValue: Float): Float = synchronized(values) { values[key] as? Float ?: defValue }
    override fun getBoolean(key: String?, defValue: Boolean): Boolean = synchronized(values) { values[key] as? Boolean ?: defValue }
    override fun contains(key: String?): Boolean = synchronized(values) { values.containsKey(key) }
    override fun edit(): SharedPreferences.Editor = Editor()
    override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) { if (l != null) listeners += l }
    override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) { if (l != null) listeners.remove(l) }

    private inner class Editor : SharedPreferences.Editor {
        private val changes = linkedMapOf<String, Any?>()
        private var clear = false
        private val removed = mutableSetOf<String>()
        override fun putString(key: String, value: String?) = apply { if (value == null) remove(key) else changes[key] = value }
        override fun putStringSet(key: String, values: MutableSet<String>?) = apply { if (values == null) remove(key) else changes[key] = values.toSet() }
        override fun putInt(key: String, value: Int) = apply { changes[key] = value }
        override fun putLong(key: String, value: Long) = apply { changes[key] = value }
        override fun putFloat(key: String, value: Float) = apply { changes[key] = value }
        override fun putBoolean(key: String, value: Boolean) = apply { changes[key] = value }
        override fun remove(key: String) = apply { removed += key; changes.remove(key) }
        override fun clear() = apply { clear = true }
        override fun commit(): Boolean {
            synchronized(values) {
                if (clear) values.clear()
                removed.forEach { values.remove(it) }
                values.putAll(changes)
            }
            return true
        }
        override fun apply() { commit() }
    }
}
