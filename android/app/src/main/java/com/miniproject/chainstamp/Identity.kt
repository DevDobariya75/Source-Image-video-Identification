package com.miniproject.chainstamp

import android.annotation.SuppressLint
import android.content.Context
import android.provider.Settings
import com.miniproject.chain.ChainPayload

/**
 * Who "this phone" is in a chain.
 *
 * Device ID: Android's ANDROID_ID (64-bit, unique per app-signing key, user
 * and device). Normal apps can't read the IMEI since Android 10, so the IMEI
 * is typed in once by the user (dial *#06# to see it) and stored locally.
 */
class Identity(context: Context) {
    private val prefs = context.getSharedPreferences("identity", Context.MODE_PRIVATE)

    @SuppressLint("HardwareIds")
    val deviceIdHex: String = (Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
        ?: "0").lowercase().padStart(16, '0').takeLast(16)

    val deviceId: ByteArray get() = ChainPayload.parseDeviceId(deviceIdHex)

    var imei: String?
        get() = prefs.getString(KEY_IMEI, null)
        set(value) = prefs.edit().putString(KEY_IMEI, value).apply()

    /** True once the user has entered an IMEI or chosen to skip it. */
    var setupDone: Boolean
        get() = prefs.getBoolean(KEY_SETUP, false)
        set(value) = prefs.edit().putBoolean(KEY_SETUP, value).apply()

    private companion object {
        const val KEY_IMEI = "imei"
        const val KEY_SETUP = "setup_done"
    }
}
