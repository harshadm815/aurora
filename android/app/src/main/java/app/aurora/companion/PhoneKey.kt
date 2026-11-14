package app.aurora.companion

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * The phone's own signing key, made and kept inside Android's Keystore (the secure hardware, where
 * the phone has it). The private half never leaves it: AURORA gets only the public half at pairing
 * and then checks that every request was signed with it.
 */
object PhoneKey {
    private const val ALIAS = "aurora-phone"
    private const val STORE = "AndroidKeyStore"

    private fun keyStore(): KeyStore = KeyStore.getInstance(STORE).apply { load(null) }

    /** A fresh key (any old one is deleted): used when pairing. Returns the public half for AURORA. */
    fun create(): String {
        val store = keyStore()
        if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS)
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, STORE)
        generator.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .build()
        )
        val pair = generator.generateKeyPair()
        return Base64.encodeToString(pair.public.encoded, Base64.NO_WRAP)
    }

    fun exists(): Boolean = keyStore().containsAlias(ALIAS)

    fun delete() {
        val store = keyStore()
        if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS)
    }

    /** Signature (ECDSA P-256 / SHA-256, DER, base64) over [data]. */
    fun sign(data: ByteArray): String {
        val key = keyStore().getKey(ALIAS, null) as PrivateKey
        val signer = Signature.getInstance("SHA256withECDSA")
        signer.initSign(key)
        signer.update(data)
        return Base64.encodeToString(signer.sign(), Base64.NO_WRAP)
    }

    /** Whether the key is in secure hardware (shown in the app; doesn't change how it works). */
    fun inHardware(): Boolean = try {
        val key = keyStore().getKey(ALIAS, null) as PrivateKey
        val factory = java.security.KeyFactory.getInstance(key.algorithm, STORE)
        val info = factory.getKeySpec(key, android.security.keystore.KeyInfo::class.java)
        info.isInsideSecureHardware
    } catch (error: Exception) {
        false
    }
}
