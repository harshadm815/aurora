package app.aurora.companion

import android.content.Context
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * AURORA's look on the phone, matching the Command Centre: near-black navy, electric cyan and blue,
 * soft glows, glass-like panels used sparingly. Everything is drawn in code (no image files), so it
 * scales to any screen and to the phone's text size.
 */
object Ui {
    class Palette(
        val background: Int, val surface: Int, val raised: Int, val line: Int,
        val text: Int, val muted: Int, val faint: Int,
        val accent: Int, val accent2: Int, val onAccent: Int,
        val ok: Int, val warn: Int, val danger: Int, val dark: Boolean,
    )

    val DARK = Palette(
        background = 0xFF070B16.toInt(), surface = 0xFF0E1528.toInt(), raised = 0xFF16203A.toInt(),
        line = 0xFF22304F.toInt(), text = 0xFFE6EDF7.toInt(), muted = 0xFF8A97B5.toInt(), faint = 0xFF56627F.toInt(),
        accent = 0xFF35D6F0.toInt(), accent2 = 0xFF4C7DFF.toInt(), onAccent = 0xFF04111A.toInt(),
        ok = 0xFF4ADE80.toInt(), warn = 0xFFF5B94A.toInt(), danger = 0xFFF26B6B.toInt(), dark = true,
    )

    val LIGHT = Palette(
        background = 0xFFF3F6FB.toInt(), surface = 0xFFFFFFFF.toInt(), raised = 0xFFE8EEF8.toInt(),
        line = 0xFFD3DCEB.toInt(), text = 0xFF0D1426.toInt(), muted = 0xFF52607D.toInt(), faint = 0xFF8A97B5.toInt(),
        accent = 0xFF0596B0.toInt(), accent2 = 0xFF2F5FE0.toInt(), onAccent = 0xFFFFFFFF.toInt(),
        ok = 0xFF16A34A.toInt(), warn = 0xFFB7791F.toInt(), danger = 0xFFDC2626.toInt(), dark = false,
    )

    /** "dark" (default), "light" or "system", kept in the app's settings. */
    fun theme(context: Context): String = Aurora.get(context).prefs.getString("theme", "dark") ?: "dark"

    fun setTheme(context: Context, value: String) = Aurora.get(context).prefs.edit().putString("theme", value).apply()

    fun palette(context: Context): Palette = when (theme(context)) {
        "light" -> LIGHT
        "system" -> if ((context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_NO) LIGHT else DARK
        else -> DARK
    }

    fun withAlpha(color: Int, alpha: Float): Int = Color.argb((alpha * 255).toInt(), Color.red(color),
        Color.green(color), Color.blue(color))

    // Type scale (sp, so the phone's text size setting applies).
    const val DISPLAY = 28f
    const val TITLE = 20f
    const val HEADING = 16f
    const val BODY = 15f
    const val SMALL = 13f
    const val CAPTION = 12f

    val regular: Typeface = Typeface.create("sans-serif", Typeface.NORMAL)
    val medium: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    val light: Typeface = Typeface.create("sans-serif-light", Typeface.NORMAL)
    val mono: Typeface = Typeface.MONOSPACE
}

/** Building blocks for the screens; [p] is the palette in use. */
class Kit(val context: Context, val p: Ui.Palette = Ui.palette(context)) {
    private val density = context.resources.displayMetrics.density
    fun px(dp: Int) = (dp * density).toInt()
    fun pxf(dp: Float) = dp * density

    fun text(value: CharSequence = "", size: Float = Ui.BODY, color: Int = p.text, face: Typeface = Ui.regular) =
        TextView(context).apply {
            text = value
            textSize = size
            setTextColor(color)
            typeface = face
            setLineSpacing(0f, 1.15f)
        }

    fun title(value: String) = text(value, Ui.TITLE, p.text, Ui.medium)

    /** Small capitals over a section, as in the Command Centre's panels. */
    fun overline(value: String) = text(value.uppercase(), Ui.CAPTION, p.muted, Ui.medium).apply { letterSpacing = 0.12f }

    fun shape(color: Int, radius: Float = 16f, stroke: Int? = null): GradientDrawable = GradientDrawable().apply {
        cornerRadius = pxf(radius)
        setColor(color)
        if (stroke != null) setStroke(px(1), stroke)
    }

    /** A rounded panel (glass-like: a little lighter than the page, a hairline edge). */
    fun card(vararg views: View, padding: Int = 16, gap: Int = 8): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = shape(p.surface, 18f, Ui.withAlpha(p.line, 0.9f))
        setPadding(px(padding), px(padding), px(padding), px(padding))
        views.forEachIndexed { i, view -> addView(view, LinearLayout.LayoutParams(-1, -2).apply { if (i > 0) topMargin = px(gap) }) }
    }

    private fun ripple(base: Drawable, radius: Float): RippleDrawable =
        RippleDrawable(ColorStateList.valueOf(Ui.withAlpha(p.accent, 0.25f)), base, shape(Color.WHITE, radius))

    fun tappable(view: View, radius: Float = 16f, onClick: () -> Unit): View = view.apply {
        background = ripple(background ?: shape(Color.TRANSPARENT, radius), radius)
        isClickable = true
        isFocusable = true
        setOnClickListener { performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP); onClick() }
    }

    /** The main action: filled cyan. */
    fun primary(label: String, onClick: () -> Unit): TextView = text(label, Ui.HEADING, p.onAccent, Ui.medium).apply {
        gravity = Gravity.CENTER
        minHeight = px(52)
        setPadding(px(20), px(12), px(20), px(12))
        background = ripple(GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(p.accent, p.accent2))
            .apply { cornerRadius = pxf(26f) }, 26f)
        isClickable = true
        setOnClickListener { performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP); onClick() }
    }

    /** A quieter action: outlined. */
    fun secondary(label: String, color: Int = p.text, onClick: () -> Unit): TextView =
        text(label, Ui.BODY, color, Ui.medium).apply {
            gravity = Gravity.CENTER
            minHeight = px(48)
            setPadding(px(16), px(10), px(16), px(10))
            background = ripple(shape(p.raised, 24f, Ui.withAlpha(p.line, 1f)), 24f)
            isClickable = true
            setOnClickListener { performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP); onClick() }
        }

    /** A round button with a symbol, at least 48 dp (easy to hit with a thumb). */
    fun round(symbol: String, description: String, size: Int = 56, fill: Int = p.raised, color: Int = p.text,
              onClick: () -> Unit): TextView = text(symbol, (size / 2.6f), color, Ui.medium).apply {
        gravity = Gravity.CENTER
        contentDescription = description
        background = ripple(GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(fill) }, size / 2f)
        isClickable = true
        setOnClickListener { performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP); onClick() }
        layoutParams = LinearLayout.LayoutParams(px(size), px(size))
    }

    /** A small coloured status label: ● Connected. */
    fun pill(value: String, color: Int): TextView = text("●  $value", Ui.SMALL, color, Ui.medium).apply {
        background = shape(Ui.withAlpha(color, 0.12f), 14f, Ui.withAlpha(color, 0.35f))
        setPadding(px(12), px(6), px(12), px(6))
    }

    fun input(hint: String): EditText = EditText(context).apply {
        this.hint = hint
        textSize = Ui.BODY
        setTextColor(p.text)
        setHintTextColor(p.faint)
        background = shape(p.raised, 22f, Ui.withAlpha(p.line, 1f))
        setPadding(px(16), px(12), px(16), px(12))
    }

    fun row(vararg views: View, gap: Int = 8, center: Boolean = true): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        if (center) gravity = Gravity.CENTER_VERTICAL
        views.forEachIndexed { i, view ->
            val params = (view.layoutParams as? LinearLayout.LayoutParams) ?: LinearLayout.LayoutParams(-2, -2)
            if (i > 0) params.leftMargin = px(gap)
            addView(view, params)
        }
    }

    fun weight(view: View, weight: Float = 1f): View = view.apply { layoutParams = LinearLayout.LayoutParams(0, -2, weight) }

    /** A scrolling page with the standard margins. */
    fun page(vararg views: View, gap: Int = 14): ScrollView = ScrollView(context).apply {
        isFillViewport = true
        isVerticalScrollBarEnabled = false
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(16), px(8), px(16), px(24))
            views.forEachIndexed { i, view ->
                addView(view, (view.layoutParams as? LinearLayout.LayoutParams)?.apply { if (i > 0) topMargin = px(gap) }
                    ?: LinearLayout.LayoutParams(-1, -2).apply { if (i > 0) topMargin = px(gap) })
            }
        }, ViewGroup.LayoutParams(-1, -2))
    }

    fun column(vararg views: View, gap: Int = 8): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        views.forEachIndexed { i, view -> addView(view, LinearLayout.LayoutParams(-1, -2).apply { if (i > 0) topMargin = px(gap) }) }
    }

    /** "Loading…", an error, or an empty state, styled the same everywhere. */
    fun note(value: String, color: Int = p.muted): TextView = text(value, Ui.SMALL, color).apply {
        setPadding(px(4), px(6), px(4), px(6))
    }
}
