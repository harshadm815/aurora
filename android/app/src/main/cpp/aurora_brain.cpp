// AURORA's mind on the phone: a small language model run by llama.cpp (offline plan C).
// One model at a time; the app calls these from one background thread (LocalBrain.kt).
// Text crosses into Kotlin as UTF-8 bytes (a token can end in the middle of a character).

#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <atomic>
#include <string>
#include <vector>

#include "llama.h"

#define LOG(...) __android_log_print(ANDROID_LOG_INFO, "AuroraBrain", __VA_ARGS__)

namespace {

struct Session {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    llama_sampler * sampler = nullptr;
    const llama_vocab * vocab = nullptr;
    std::atomic<bool> stop{false};
    std::vector<llama_token> cached;     // what the model has already read (its memory), in order
};

std::vector<llama_token> tokenize(Session * s, const std::string & text) {
    std::vector<llama_token> tokens(text.size() + 16);
    int32_t n = llama_tokenize(s->vocab, text.c_str(), (int32_t) text.size(), tokens.data(), (int32_t) tokens.size(),
                               false, true);
    if (n < 0) {
        tokens.resize(-n);
        n = llama_tokenize(s->vocab, text.c_str(), (int32_t) text.size(), tokens.data(), (int32_t) tokens.size(),
                           false, true);
    }
    tokens.resize(n > 0 ? n : 0);
    return tokens;
}

// Makes the model's memory hold exactly [tokens], reading only what differs from what it already holds
// (the instructions and earlier messages are usually the same: the main saving). False if stopped or failed.
bool prefill(Session * s, const std::vector<llama_token> & tokens) {
    size_t common = 0;
    while (common < s->cached.size() && common < tokens.size() && s->cached[common] == tokens[common]) common++;
    if (common == tokens.size() && common > 0) common--;          // the last one is read again, for its logits
    llama_memory_t mem = llama_get_memory(s->ctx);
    if (common == 0 || !llama_memory_seq_rm(mem, 0, (llama_pos) common, -1)) {
        llama_memory_clear(mem, true);
        common = 0;
    }
    s->cached.assign(tokens.begin(), tokens.begin() + common);
    for (size_t at = common; at < tokens.size(); at += 256) {       // the rest, in pieces of the batch size
        const int32_t count = (int32_t) std::min<size_t>(256, tokens.size() - at);
        if (llama_decode(s->ctx, llama_batch_get_one(const_cast<llama_token *>(tokens.data()) + at, count)) != 0) {
            llama_memory_clear(mem, true);
            s->cached.clear();
            return false;
        }
        s->cached.insert(s->cached.end(), tokens.begin() + at, tokens.begin() + at + count);
        if (s->stop) return false;
    }
    return true;
}

jbyteArray bytes(JNIEnv * env, const std::string & text) {
    jbyteArray out = env->NewByteArray((jsize) text.size());
    env->SetByteArrayRegion(out, 0, (jsize) text.size(), reinterpret_cast<const jbyte *>(text.data()));
    return out;
}

std::string str(JNIEnv * env, jstring value) {
    const char * chars = env->GetStringUTFChars(value, nullptr);
    std::string result(chars ? chars : "");
    env->ReleaseStringUTFChars(value, chars);
    return result;
}

}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_app_aurora_companion_LocalBrain_nativeLoad(JNIEnv * env, jobject, jstring path, jint threads, jint context) {
    static bool started = false;
    if (!started) { llama_backend_init(); started = true; }
    auto * s = new Session();
    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;                                   // CPU only (no GPU code built)
    s->model = llama_model_load_from_file(str(env, path).c_str(), mp);
    if (!s->model) { delete s; return 0; }
    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = (uint32_t) context;
    cp.n_batch = 256;
    cp.n_threads = threads;
    cp.n_threads_batch = threads;
    s->ctx = llama_init_from_model(s->model, cp);
    if (!s->ctx) { llama_model_free(s->model); delete s; return 0; }
    s->vocab = llama_model_get_vocab(s->model);
    s->sampler = llama_sampler_chain_init(llama_sampler_chain_default_params());
    llama_sampler_chain_add(s->sampler, llama_sampler_init_top_k(40));
    llama_sampler_chain_add(s->sampler, llama_sampler_init_top_p(0.9f, 1));
    llama_sampler_chain_add(s->sampler, llama_sampler_init_temp(0.7f));
    llama_sampler_chain_add(s->sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    LOG("model loaded (%d threads, context %d)", threads, context);
    return reinterpret_cast<jlong>(s);
}

// The conversation formatted with the model's own chat template (ChatML for Qwen3), ready to continue.
extern "C" JNIEXPORT jbyteArray JNICALL
Java_app_aurora_companion_LocalBrain_nativeFormat(JNIEnv * env, jobject, jlong handle, jobjectArray roles,
                                                  jobjectArray contents) {
    auto * s = reinterpret_cast<Session *>(handle);
    const jsize n = env->GetArrayLength(roles);
    std::vector<std::string> r(n), c(n);
    std::vector<llama_chat_message> chat(n);
    for (jsize i = 0; i < n; i++) {
        r[i] = str(env, (jstring) env->GetObjectArrayElement(roles, i));
        c[i] = str(env, (jstring) env->GetObjectArrayElement(contents, i));
    }
    for (jsize i = 0; i < n; i++) chat[i] = {r[i].c_str(), c[i].c_str()};
    const char * tmpl = llama_model_chat_template(s->model, nullptr);
    std::vector<char> buf(4096);
    int32_t len = llama_chat_apply_template(tmpl, chat.data(), chat.size(), true, buf.data(), (int32_t) buf.size());
    if (len > (int32_t) buf.size()) {
        buf.resize(len);
        len = llama_chat_apply_template(tmpl, chat.data(), chat.size(), true, buf.data(), (int32_t) buf.size());
    }
    if (len < 0) return bytes(env, "");
    return bytes(env, std::string(buf.data(), len));
}

// Writes a reply to [prompt]; [listener].onPiece(bytes) gets each piece as it comes. Returns all of it.
extern "C" JNIEXPORT jbyteArray JNICALL
Java_app_aurora_companion_LocalBrain_nativeGenerate(JNIEnv * env, jobject, jlong handle, jstring prompt,
                                                    jint max_tokens, jobject listener) {
    auto * s = reinterpret_cast<Session *>(handle);
    s->stop = false;
    llama_sampler_reset(s->sampler);

    std::vector<llama_token> tokens = tokenize(s, str(env, prompt));
    if (tokens.empty()) return bytes(env, "");
    const int32_t room = (int32_t) llama_n_ctx(s->ctx) - max_tokens - 8;
    if ((int32_t) tokens.size() > room) tokens.erase(tokens.begin(), tokens.begin() + ((int32_t) tokens.size() - room));
    if (!prefill(s, tokens)) return bytes(env, "");

    jclass type = env->GetObjectClass(listener);
    jmethodID on_piece = env->GetMethodID(type, "onPiece", "([B)V");
    std::string reply;
    char piece[256];
    for (int i = 0; i < max_tokens && !s->stop; i++) {
        llama_token token = llama_sampler_sample(s->sampler, s->ctx, -1);
        if (llama_vocab_is_eog(s->vocab, token)) break;
        const int32_t size = llama_token_to_piece(s->vocab, token, piece, sizeof(piece), 0, false);
        if (size > 0) {
            reply.append(piece, size);
            jbyteArray chunk = bytes(env, std::string(piece, size));
            env->CallVoidMethod(listener, on_piece, chunk);
            env->DeleteLocalRef(chunk);
        }
        if (llama_decode(s->ctx, llama_batch_get_one(&token, 1)) != 0) break;
        s->cached.push_back(token);
    }
    return bytes(env, reply);
}

// Reads [prompt] ahead of time (the instructions), so the first real question starts quickly.
extern "C" JNIEXPORT jboolean JNICALL
Java_app_aurora_companion_LocalBrain_nativePrime(JNIEnv * env, jobject, jlong handle, jstring prompt) {
    auto * s = reinterpret_cast<Session *>(handle);
    s->stop = false;
    std::vector<llama_token> tokens = tokenize(s, str(env, prompt));
    return (jboolean) (!tokens.empty() && prefill(s, tokens));
}

extern "C" JNIEXPORT void JNICALL
Java_app_aurora_companion_LocalBrain_nativeStop(JNIEnv *, jobject, jlong handle) {
    if (handle) reinterpret_cast<Session *>(handle)->stop = true;
}

extern "C" JNIEXPORT void JNICALL
Java_app_aurora_companion_LocalBrain_nativeFree(JNIEnv *, jobject, jlong handle) {
    auto * s = reinterpret_cast<Session *>(handle);
    if (!s) return;
    llama_sampler_free(s->sampler);
    llama_free(s->ctx);
    llama_model_free(s->model);
    delete s;
    LOG("model released");
}
