/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * frame_dump.cpp — see frame_dump.h.
 *
 * Build deps: gstreamer-1.0, json-glib-1.0, DeepStream includes,
 *             libnvds_batch_jpegenc, libnvbufsurface, libdl. No civetweb /
 *             rest_server. The Kafka notifier loads the nvds_msgapi Kafka
 *             protocol adaptor with dlopen() -- no extra link dependency.
 *
 * NOTE: this module lives in the mixed C/C++ apps-common lib, which is built
 * with C++ exceptions and RTTI disabled (a C++-only codegen flag cannot be set
 * on a component that also compiles C sources). The YAML config block is parsed
 * with a tiny hand-rolled scalar reader instead of yaml-cpp for that reason.
 */
#include "frame_dump.h"

#include <gstnvdsmeta.h>
#include <nvdsmeta.h>
#include <nvbufsurface.h>
#include "nvds_obj_encode.h"

#include <json-glib/json-glib.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>

#include <arpa/inet.h>
#include <dirent.h>
#include <ftw.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <ctime>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <cstdint>
#include <dlfcn.h>

/* ------------------------------------------------------------------ */
/* Shared control state (written by REST threads, read by the probe). */
/* ------------------------------------------------------------------ */
struct DumpControl {
  std::atomic<bool>     enabled{false};        /* global on/off              */
  std::atomic<uint32_t> fps{1};                /* target dump fps            */
  std::atomic<int>      quality{80};           /* JPEG quality               */
  std::atomic<bool>     hour_subfolder{false}; /* add /<HH>/ level           */
  std::atomic<bool>     unique_names{false};   /* <frame_num>_<pts>          */
  std::atomic<uint32_t> retention_days{0};     /* 0 = keep forever           */
  std::atomic<bool>     all_cameras{false};    /* dump every source          */

  std::mutex            cfg_mtx;               /* guards output_dir + on_set */
  std::string           output_dir;
  std::unordered_set<std::string> on_sensors;  /* cameras switched ON        */

  std::mutex            gate_mtx;              /* guards last_pts            */
  std::unordered_map<guint, uint64_t> last_pts;

  /* dump this sensor if all_cameras is on, or it's in the ON set */
  bool should_dump(const std::string &s) {
    if (all_cameras.load()) return true;
    std::lock_guard<std::mutex> lk(cfg_mtx);
    return on_sensors.count(s) > 0;
  }
  std::string dir() {
    std::lock_guard<std::mutex> lk(cfg_mtx);
    return output_dir;
  }
};

static DumpControl        g_ctrl;
static NvDsObjEncCtxHandle g_enc = nullptr;
static std::string        g_bind_addr = "0.0.0.0";

/* ------------------------------------------------------------------ */
/* Kafka notifier: publish a JSON message per dumped frame via the    */
/* DeepStream nvds_msgapi Kafka protocol adaptor, dlopen'd at runtime */
/* (no extra link dependency -- only libdl, already linked).          */
/* ------------------------------------------------------------------ */
/* ABI-compatible local typedefs avoid an nvds_msgapi.h include-path
 * dependency; NvDsMsgApiErrorType/EventType are plain C enums (== int). */
typedef void *NvDsMsgApiHandle;
typedef void (*msgapi_connect_cb)(NvDsMsgApiHandle, int);
typedef void (*msgapi_send_cb)(void *, int);
typedef NvDsMsgApiHandle (*fn_connect)(char *, msgapi_connect_cb, char *);
typedef int  (*fn_send_async)(NvDsMsgApiHandle, char *, const uint8_t *, size_t, msgapi_send_cb, void *);
typedef int  (*fn_disconnect)(NvDsMsgApiHandle);
typedef void (*fn_do_work)(NvDsMsgApiHandle);

struct KafkaState {
  std::atomic<bool> enabled{false};
  std::mutex        mtx;                 /* guards the strings below            */
  std::string       broker, topic, proto_lib, config_file;

  void            *lib    = nullptr;     /* dlopen'd adaptor                    */
  NvDsMsgApiHandle handle = nullptr;     /* live connection                     */
  fn_connect       connect    = nullptr;
  fn_send_async    send_async = nullptr;
  fn_disconnect    disconnect = nullptr;
  fn_do_work       do_work    = nullptr;
  std::mutex       send_mtx;             /* serialize send_async + do_work      */
};
static KafkaState g_kafka;

/* Load the adaptor and connect. Called once from frame_dump_init when Kafka is
 * configured. Any failure just leaves Kafka off; frame dumping is unaffected. */
static void kafka_connect() {
  std::string lib, broker, cfg;
  { std::lock_guard<std::mutex> lk(g_kafka.mtx);
    lib    = g_kafka.proto_lib.empty()
               ? "/opt/nvidia/deepstream/deepstream/lib/libnvds_kafka_proto.so"
               : g_kafka.proto_lib;
    broker = g_kafka.broker;
    cfg    = g_kafka.config_file;
  }
  if (broker.empty()) { GST_WARNING("frame_dump: kafka enabled but no broker set"); return; }

  /* RTLD_LOCAL (not RTLD_GLOBAL): the adaptor is used only via dlsym on this
   * handle, so its symbols must NOT enter the global scope. RTLD_GLOBAL pulls the
   * adaptor's dependency graph (librdkafka, libstdc++, libssl/crypto, glib) into
   * the global namespace and perturbs symbol resolution for other DeepStream
   * plugins loaded later -- e.g. it makes a mismatched libprotobuf bind in
   * nvmsgconv (GOOGLE_PROTOBUF_VERIFY_VERSION fatal -> terminate()). Keep it local. */
  g_kafka.lib = dlopen(lib.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!g_kafka.lib) { GST_ERROR("frame_dump: kafka dlopen(%s) failed: %s", lib.c_str(), dlerror()); return; }
  g_kafka.connect    = (fn_connect)    dlsym(g_kafka.lib, "nvds_msgapi_connect");
  g_kafka.send_async = (fn_send_async) dlsym(g_kafka.lib, "nvds_msgapi_send_async");
  g_kafka.disconnect = (fn_disconnect) dlsym(g_kafka.lib, "nvds_msgapi_disconnect");
  g_kafka.do_work    = (fn_do_work)    dlsym(g_kafka.lib, "nvds_msgapi_do_work");
  if (!g_kafka.connect || !g_kafka.send_async) {
    GST_ERROR("frame_dump: kafka adaptor missing nvds_msgapi symbols");
    dlclose(g_kafka.lib); g_kafka.lib = nullptr; return;
  }
  char *cfgp = cfg.empty() ? nullptr : (char *) cfg.c_str();
  g_kafka.handle = g_kafka.connect((char *) broker.c_str(), nullptr, cfgp);
  if (!g_kafka.handle) { GST_ERROR("frame_dump: kafka connect to %s failed", broker.c_str()); return; }
  g_print("frame_dump: kafka connected to %s\n", broker.c_str());
}

/* Publish one notification (called from the writer thread). Non-blocking. */
static void kafka_publish(const std::string &payload) {
  if (!g_kafka.enabled.load() || !g_kafka.handle || !g_kafka.send_async) return;
  std::string topic;
  { std::lock_guard<std::mutex> lk(g_kafka.mtx); topic = g_kafka.topic; }
  if (topic.empty()) return;
  std::lock_guard<std::mutex> lk(g_kafka.send_mtx);
  g_kafka.send_async(g_kafka.handle, (char *) topic.c_str(),
                     (const uint8_t *) payload.data(), payload.size(), nullptr, nullptr);
  if (g_kafka.do_work) g_kafka.do_work(g_kafka.handle);   /* flush rdkafka queue */
}

/* ------------------------------------------------------------------ */
/* Threads / lifecycle.                                               */
/* ------------------------------------------------------------------ */
static std::atomic<bool>       g_running{false};
static std::thread             g_writer, g_http, g_retention;
static std::condition_variable g_ret_cv;
static std::mutex              g_ret_mtx;

/* ------------------------------------------------------------------ */
/* Async .txt writer (JPEG is written by nvds_obj_enc itself).        */
/* ------------------------------------------------------------------ */
struct WriteJob { std::string path; std::string content; std::string kafka; };
static std::queue<WriteJob>    g_wq;
static std::mutex              g_wq_mtx;
static std::condition_variable g_wq_cv;

static void writer_loop() {
  while (g_running.load()) {
    WriteJob job;
    {
      std::unique_lock<std::mutex> lk(g_wq_mtx);
      g_wq_cv.wait(lk, [] { return !g_wq.empty() || !g_running.load(); });
      if (!g_running.load() && g_wq.empty()) break;
      job = std::move(g_wq.front());
      g_wq.pop();
    }
    FILE *fp = fopen(job.path.c_str(), "w");
    if (fp) { fwrite(job.content.data(), 1, job.content.size(), fp); fclose(fp); }
    if (!job.kafka.empty()) kafka_publish(job.kafka);
  }
}
static void writer_enqueue(std::string path, std::string content, std::string kafka = std::string()) {
  {
    std::lock_guard<std::mutex> lk(g_wq_mtx);
    g_wq.push(WriteJob{std::move(path), std::move(content), std::move(kafka)});
  }
  g_wq_cv.notify_one();
}

/* ------------------------------------------------------------------ */
/* Filesystem helpers.                                                */
/* ------------------------------------------------------------------ */
static std::mutex                     g_dir_mtx;
static std::unordered_set<std::string> g_dir_cache;

static void mkdir_p(const std::string &path) {
  std::string cur;
  for (size_t i = 0; i < path.size(); ++i) {
    cur += path[i];
    if (path[i] == '/' || i + 1 == path.size())
      if (cur.size() > 1) mkdir(cur.c_str(), 0775);
  }
}
static void ensure_dir(const std::string &dir) {
  {
    std::lock_guard<std::mutex> lk(g_dir_mtx);
    if (g_dir_cache.count(dir)) return;
  }
  mkdir_p(dir);
  std::lock_guard<std::mutex> lk(g_dir_mtx);
  g_dir_cache.insert(dir);
}
static std::string tstr(const char *fmt) {
  time_t t = time(nullptr); struct tm tmv; localtime_r(&t, &tmv);
  char b[32]; strftime(b, sizeof(b), fmt, &tmv); return std::string(b);
}

/* recursive delete (nftw, depth-first) */
static int rm_cb(const char *p, const struct stat *, int, struct FTW *) { remove(p); return 0; }
static void rm_rf(const std::string &path) { nftw(path.c_str(), rm_cb, 16, FTW_DEPTH | FTW_PHYS); }

/* ------------------------------------------------------------------ */
/* Retention sweeper: delete <base>/<sensor>/<YYYY-MM-DD> older than N.*/
/* ------------------------------------------------------------------ */
static void retention_loop() {
  while (g_running.load()) {
    {   /* wake hourly, or immediately on shutdown */
      std::unique_lock<std::mutex> lk(g_ret_mtx);
      g_ret_cv.wait_for(lk, std::chrono::hours(1), [] { return !g_running.load(); });
    }
    if (!g_running.load()) break;
    uint32_t days = g_ctrl.retention_days.load();
    if (days == 0) continue;

    time_t now = time(nullptr); struct tm tmv; localtime_r(&now, &tmv);
    tmv.tm_hour = tmv.tm_min = tmv.tm_sec = 0;
    time_t cutoff = mktime(&tmv) - (time_t) days * 86400;   /* midnight - N days */

    std::string base = g_ctrl.dir();
    DIR *bd = opendir(base.c_str()); if (!bd) continue;
    struct dirent *se;
    bool removed = false;
    while ((se = readdir(bd))) {
      if (se->d_name[0] == '.') continue;
      std::string sdir = base + "/" + se->d_name;
      struct stat st; if (stat(sdir.c_str(), &st) || !S_ISDIR(st.st_mode)) continue;
      DIR *dd = opendir(sdir.c_str()); if (!dd) continue;
      struct dirent *de;
      while ((de = readdir(dd))) {
        if (de->d_name[0] == '.') continue;
        struct tm dtm; memset(&dtm, 0, sizeof(dtm));
        if (strptime(de->d_name, "%Y-%m-%d", &dtm)) {
          time_t dayt = mktime(&dtm);
          if (dayt > 0 && dayt < cutoff) { rm_rf(sdir + "/" + de->d_name); removed = true; }
        }
      }
      closedir(dd);
    }
    closedir(bd);
    if (removed) { std::lock_guard<std::mutex> lk(g_dir_mtx); g_dir_cache.clear(); }
  }
}

/* ------------------------------------------------------------------ */
/* Per-frame metadata -> JSON.                                        */
/* ------------------------------------------------------------------ */
/* serialize a JsonBuilder root to a std::string (pretty) */
static std::string jb_to_string(JsonBuilder *b) {
  JsonGenerator *gen = json_generator_new();
  json_generator_set_root(gen, json_builder_get_root(b));
  json_generator_set_pretty(gen, TRUE);
  gchar *s = json_generator_to_data(gen, nullptr);
  std::string out(s ? s : "");
  g_free(s); g_object_unref(gen);
  return out;
}
static void jb_bbox(JsonBuilder *b, const char *name,
                    float left, float top, float width, float height) {
  json_builder_set_member_name(b, name);
  json_builder_begin_object(b);
  json_builder_set_member_name(b, "left");   json_builder_add_double_value(b, left);
  json_builder_set_member_name(b, "top");    json_builder_add_double_value(b, top);
  json_builder_set_member_name(b, "width");  json_builder_add_double_value(b, width);
  json_builder_set_member_name(b, "height"); json_builder_add_double_value(b, height);
  json_builder_end_object(b);
}

static std::string build_meta_json(NvDsFrameMeta *fm, const std::string &sensor) {
  JsonBuilder *b = json_builder_new();
  json_builder_begin_object(b);
  json_builder_set_member_name(b, "sensor_id");     json_builder_add_string_value(b, sensor.c_str());
  json_builder_set_member_name(b, "source_id");     json_builder_add_int_value(b, fm->source_id);
  json_builder_set_member_name(b, "frame_num");     json_builder_add_int_value(b, fm->frame_num);
  json_builder_set_member_name(b, "buf_pts");       json_builder_add_int_value(b, (gint64) fm->buf_pts);
  json_builder_set_member_name(b, "ntp_timestamp"); json_builder_add_int_value(b, (gint64) fm->ntp_timestamp);
  json_builder_set_member_name(b, "wall_time");     json_builder_add_string_value(b, tstr("%Y-%m-%dT%H:%M:%S").c_str());
  json_builder_set_member_name(b, "frame_width");   json_builder_add_int_value(b, fm->pipeline_width);
  json_builder_set_member_name(b, "frame_height");  json_builder_add_int_value(b, fm->pipeline_height);

  json_builder_set_member_name(b, "objects");
  json_builder_begin_array(b);
  for (NvDsMetaList *l = fm->obj_meta_list; l; l = l->next) {
    NvDsObjectMeta *o = (NvDsObjectMeta *) l->data;
    if (!o) continue;
    json_builder_begin_object(b);
    json_builder_set_member_name(b, "class_id");   json_builder_add_int_value(b, o->class_id);
    json_builder_set_member_name(b, "label");      json_builder_add_string_value(b, o->obj_label[0] ? o->obj_label : "");
    json_builder_set_member_name(b, "object_id");  json_builder_add_int_value(b, (gint64) o->object_id);
    json_builder_set_member_name(b, "confidence"); json_builder_add_double_value(b, o->confidence);
    jb_bbox(b, "detector_bbox",
            o->detector_bbox_info.org_bbox_coords.left, o->detector_bbox_info.org_bbox_coords.top,
            o->detector_bbox_info.org_bbox_coords.width, o->detector_bbox_info.org_bbox_coords.height);
    jb_bbox(b, "tracker_bbox",
            o->tracker_bbox_info.org_bbox_coords.left, o->tracker_bbox_info.org_bbox_coords.top,
            o->tracker_bbox_info.org_bbox_coords.width, o->tracker_bbox_info.org_bbox_coords.height);
    json_builder_end_object(b);
  }
  json_builder_end_array(b);
  json_builder_end_object(b);
  std::string out = jb_to_string(b);
  g_object_unref(b);
  return out;
}

/* Compact notification for Kafka: which files were written + key metadata. */
static std::string build_kafka_json(NvDsFrameMeta *fm, const std::string &sensor,
                                    const std::string &jpg, const std::string &txt,
                                    guint num_objects) {
  JsonBuilder *b = json_builder_new();
  json_builder_begin_object(b);
  json_builder_set_member_name(b, "event");         json_builder_add_string_value(b, "frame_dumped");
  json_builder_set_member_name(b, "sensor_id");     json_builder_add_string_value(b, sensor.c_str());
  json_builder_set_member_name(b, "source_id");     json_builder_add_int_value(b, fm->source_id);
  json_builder_set_member_name(b, "frame_num");     json_builder_add_int_value(b, fm->frame_num);
  json_builder_set_member_name(b, "buf_pts");       json_builder_add_int_value(b, (gint64) fm->buf_pts);
  json_builder_set_member_name(b, "ntp_timestamp"); json_builder_add_int_value(b, (gint64) fm->ntp_timestamp);
  json_builder_set_member_name(b, "wall_time");     json_builder_add_string_value(b, tstr("%Y-%m-%dT%H:%M:%S").c_str());
  json_builder_set_member_name(b, "jpg_path");      json_builder_add_string_value(b, jpg.c_str());
  json_builder_set_member_name(b, "txt_path");      json_builder_add_string_value(b, txt.c_str());
  json_builder_set_member_name(b, "num_objects");   json_builder_add_int_value(b, num_objects);
  json_builder_end_object(b);
  std::string out = jb_to_string(b);
  g_object_unref(b);
  return out;
}

/* ------------------------------------------------------------------ */
/* The probe on the nvtracker SRC pad.                                */
/* ------------------------------------------------------------------ */
static GstPadProbeReturn
frame_dump_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data) {
  (void) pad; (void) user_data;
  if (!g_ctrl.enabled.load()) return GST_PAD_PROBE_OK;

  GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
  if (!buf) return GST_PAD_PROBE_OK;
  GstMapInfo inmap;
  if (!gst_buffer_map(buf, &inmap, GST_MAP_READ)) return GST_PAD_PROBE_OK;
  NvBufSurface *surf = (NvBufSurface *) inmap.data;
  NvDsBatchMeta *bm = gst_buffer_get_nvds_batch_meta(buf);
  if (!surf || !bm) { gst_buffer_unmap(buf, &inmap); return GST_PAD_PROBE_OK; }

  uint32_t fps = g_ctrl.fps.load(); if (fps == 0) fps = 1;
  const uint64_t interval_ns = 1000000000ULL / fps;
  const std::string base_dir = g_ctrl.dir();
  const std::string day  = tstr("%Y-%m-%d");
  const bool hour_sub = g_ctrl.hour_subfolder.load();
  const std::string hour = hour_sub ? ("/" + tstr("%H")) : "";
  const bool uniq = g_ctrl.unique_names.load();
  bool encoded_any = false;

  for (NvDsMetaList *l = bm->frame_meta_list; l; l = l->next) {
    NvDsFrameMeta *fm = (NvDsFrameMeta *) l->data;
    if (!fm) continue;

    const char *sid = fm->sensorInfo_meta.sensor_id;
    std::string sensor = (sid && sid[0]) ? std::string(sid)
                                         : ("source_" + std::to_string(fm->source_id));
    if (!g_ctrl.should_dump(sensor)) continue;

    uint64_t t = fm->buf_pts;
    if (t == (uint64_t) GST_CLOCK_TIME_NONE || t == 0) {
      struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
      t = (uint64_t) ts.tv_sec * 1000000000ULL + ts.tv_nsec;
    }
    {
      std::lock_guard<std::mutex> lk(g_ctrl.gate_mtx);
      uint64_t &last = g_ctrl.last_pts[fm->source_id];
      if (last != 0 && t >= last && (t - last) < interval_ns) continue;
      last = t;
    }

    std::string dir = base_dir + "/" + sensor + "/" + day + hour;
    ensure_dir(dir);

    char stem[96];
    if (uniq) snprintf(stem, sizeof(stem), "%d_%llu", fm->frame_num, (unsigned long long) t);
    else      snprintf(stem, sizeof(stem), "%d", fm->frame_num);

    std::string jpg_path = dir + "/" + stem + ".jpg";
    std::string txt_path = dir + "/" + stem + ".txt";

    NvDsObjEncUsrArgs args; memset(&args, 0, sizeof(args));
    args.saveImg = true; args.isFrame = 1; args.quality = g_ctrl.quality.load();
    snprintf(args.fileNameImg, sizeof(args.fileNameImg), "%s", jpg_path.c_str());
    nvds_obj_enc_process(g_enc, &args, surf, nullptr, fm);
    encoded_any = true;

    /* Build the Kafka notification alongside the .txt; the writer thread
     * publishes it so the streaming thread stays lean. */
    std::string kj;
    if (g_kafka.enabled.load()) {
      guint nobj = 0;
      for (NvDsMetaList *o = fm->obj_meta_list; o; o = o->next) ++nobj;
      kj = build_kafka_json(fm, sensor, jpg_path, txt_path, nobj);
    }
    writer_enqueue(txt_path, build_meta_json(fm, sensor), std::move(kj));
  }

  if (encoded_any) nvds_obj_enc_finish(g_enc);
  gst_buffer_unmap(buf, &inmap);
  return GST_PAD_PROBE_OK;
}

/* ------------------------------------------------------------------ */
/* Minimal HTTP control server.                                       */
/* ------------------------------------------------------------------ */
static int g_listen_fd = -1;

/* Parse a request body into a JsonObject. Returns the owning parser (caller
 * must g_object_unref) or nullptr on error; *objOut points into the parser. */
static JsonParser *json_parse_body(const std::string &body, JsonObject **objOut) {
  if (body.empty()) return nullptr;
  JsonParser *p = json_parser_new();
  GError *err = nullptr;
  if (!json_parser_load_from_data(p, body.c_str(), body.size(), &err)) {
    if (err) g_error_free(err);
    g_object_unref(p); return nullptr;
  }
  JsonNode *root = json_parser_get_root(p);
  if (!root || !JSON_NODE_HOLDS_OBJECT(root)) { g_object_unref(p); return nullptr; }
  *objOut = json_node_get_object(root);
  return p;
}
/* type-coercing accessors (accept 1/0 for booleans) */
static bool jo_has(JsonObject *o, const char *k) { return json_object_has_member(o, k); }
static bool jo_bool(JsonObject *o, const char *k) {
  JsonNode *n = json_object_get_member(o, k); if (!n) return false;
  GType t = json_node_get_value_type(n);
  if (t == G_TYPE_BOOLEAN) return json_node_get_boolean(n);
  if (t == G_TYPE_INT64)   return json_node_get_int(n) != 0;
  return false;
}
static gint64 jo_int(JsonObject *o, const char *k) {
  JsonNode *n = json_object_get_member(o, k);
  return (n && json_node_get_value_type(n) == G_TYPE_INT64) ? json_node_get_int(n) : 0;
}
static std::string jo_str(JsonObject *o, const char *k) {
  JsonNode *n = json_object_get_member(o, k);
  if (n && json_node_get_value_type(n) == G_TYPE_STRING) {
    const char *s = json_node_get_string(n); return s ? s : "";
  }
  return "";
}

static std::string status_json() {
  JsonBuilder *b = json_builder_new();
  json_builder_begin_object(b);
  json_builder_set_member_name(b, "enabled");          json_builder_add_boolean_value(b, g_ctrl.enabled.load());
  json_builder_set_member_name(b, "fps");              json_builder_add_int_value(b, g_ctrl.fps.load());
  json_builder_set_member_name(b, "quality");          json_builder_add_int_value(b, g_ctrl.quality.load());
  json_builder_set_member_name(b, "hour_subfolder");   json_builder_add_boolean_value(b, g_ctrl.hour_subfolder.load());
  json_builder_set_member_name(b, "unique_filenames"); json_builder_add_boolean_value(b, g_ctrl.unique_names.load());
  json_builder_set_member_name(b, "retention_days");   json_builder_add_int_value(b, g_ctrl.retention_days.load());
  json_builder_set_member_name(b, "all_cameras");      json_builder_add_boolean_value(b, g_ctrl.all_cameras.load());
  json_builder_set_member_name(b, "kafka_enable");     json_builder_add_boolean_value(b, g_kafka.enabled.load());
  json_builder_set_member_name(b, "kafka_connected");  json_builder_add_boolean_value(b, g_kafka.handle != nullptr);
  {
    std::lock_guard<std::mutex> lk(g_kafka.mtx);
    json_builder_set_member_name(b, "kafka_broker"); json_builder_add_string_value(b, g_kafka.broker.c_str());
    json_builder_set_member_name(b, "kafka_topic");  json_builder_add_string_value(b, g_kafka.topic.c_str());
  }
  {
    std::lock_guard<std::mutex> lk(g_ctrl.cfg_mtx);
    json_builder_set_member_name(b, "location"); json_builder_add_string_value(b, g_ctrl.output_dir.c_str());
    json_builder_set_member_name(b, "cameras_on");
    json_builder_begin_array(b);
    for (auto &s : g_ctrl.on_sensors) json_builder_add_string_value(b, s.c_str());
    json_builder_end_array(b);
  }
  json_builder_end_object(b);
  std::string out = jb_to_string(b);
  g_object_unref(b);
  return out;
}
static std::string http_resp(const char *status, const std::string &b) {
  return std::string("HTTP/1.1 ") + status +
         "\r\nContent-Type: application/json\r\nContent-Length: " +
         std::to_string(b.size()) + "\r\nConnection: close\r\n\r\n" + b;
}
static std::string handle_request(const std::string &method, const std::string &path,
                                  const std::string &body) {
  if (method == "GET" && path == "/dump/status") return http_resp("200 OK", status_json());

  if (method == "POST" && path == "/dump/config") {
    JsonObject *j = nullptr;
    JsonParser *p = json_parse_body(body, &j);
    if (!p) return http_resp("400 Bad Request", "{\"error\":\"invalid json\"}");
    if (jo_has(j, "enable"))           g_ctrl.enabled.store(jo_bool(j, "enable"));
    if (jo_has(j, "fps"))              { gint64 v = jo_int(j, "fps"); g_ctrl.fps.store(v > 0 ? (uint32_t) v : 1); }
    if (jo_has(j, "quality"))          g_ctrl.quality.store((int) jo_int(j, "quality"));
    if (jo_has(j, "hour_subfolder"))   g_ctrl.hour_subfolder.store(jo_bool(j, "hour_subfolder"));
    if (jo_has(j, "unique_filenames")) g_ctrl.unique_names.store(jo_bool(j, "unique_filenames"));
    if (jo_has(j, "retention_days"))   g_ctrl.retention_days.store((uint32_t) jo_int(j, "retention_days"));
    if (jo_has(j, "all_cameras"))      g_ctrl.all_cameras.store(jo_bool(j, "all_cameras"));
    if (jo_has(j, "kafka_enable"))     g_kafka.enabled.store(jo_bool(j, "kafka_enable"));
    if (jo_has(j, "kafka_topic")) {
      std::lock_guard<std::mutex> lk(g_kafka.mtx);
      g_kafka.topic = jo_str(j, "kafka_topic");
    }
    if (jo_has(j, "sensors")) {
      JsonNode *sn = json_object_get_member(j, "sensors");
      if (sn && JSON_NODE_HOLDS_ARRAY(sn)) {
        JsonArray *arr = json_node_get_array(sn);
        std::lock_guard<std::mutex> lk(g_ctrl.cfg_mtx);
        for (guint i = 0, n = json_array_get_length(arr); i < n; ++i) {
          const char *s = json_array_get_string_element(arr, i);
          if (s) g_ctrl.on_sensors.insert(s);
        }
      }
    }
    if (jo_has(j, "location")) {
      std::lock_guard<std::mutex> lk(g_ctrl.cfg_mtx);
      g_ctrl.output_dir = jo_str(j, "location");
    }
    std::string resp = http_resp("200 OK", status_json());
    g_object_unref(p);
    return resp;
  }

  if (method == "POST" && path == "/dump/camera") {
    JsonObject *j = nullptr;
    JsonParser *p = json_parse_body(body, &j);
    if (!p || !jo_has(j, "sensor_id") || !jo_has(j, "enable")) {
      if (p) g_object_unref(p);
      return http_resp("400 Bad Request", "{\"error\":\"need sensor_id + enable\"}");
    }
    std::string sid = jo_str(j, "sensor_id"); bool en = jo_bool(j, "enable");
    {
      std::lock_guard<std::mutex> lk(g_ctrl.cfg_mtx);
      if (en) g_ctrl.on_sensors.insert(sid); else g_ctrl.on_sensors.erase(sid);
    }
    std::string resp = http_resp("200 OK", status_json());
    g_object_unref(p);
    return resp;
  }
  return http_resp("404 Not Found", "{\"error\":\"not found\"}");
}

static void http_loop(int port) {
  g_listen_fd = socket(AF_INET, SOCK_STREAM, 0);
  if (g_listen_fd < 0) { GST_ERROR("frame_dump: socket() failed"); return; }
  int on = 1; setsockopt(g_listen_fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
  struct sockaddr_in addr; memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = (g_bind_addr.empty() || g_bind_addr == "0.0.0.0")
                           ? htonl(INADDR_ANY) : inet_addr(g_bind_addr.c_str());
  addr.sin_port = htons((uint16_t) port);
  if (bind(g_listen_fd, (struct sockaddr *) &addr, sizeof(addr)) < 0) {
    GST_ERROR("frame_dump: bind(%s:%d) failed: %s", g_bind_addr.c_str(), port, strerror(errno));
    close(g_listen_fd); g_listen_fd = -1; return;
  }
  listen(g_listen_fd, 8);
  g_print("frame_dump: control server on %s:%d\n", g_bind_addr.c_str(), port);

  while (g_running.load()) {
    struct timeval tv{1, 0};
    fd_set fds; FD_ZERO(&fds); FD_SET(g_listen_fd, &fds);
    if (select(g_listen_fd + 1, &fds, nullptr, nullptr, &tv) <= 0) continue;
    int c = accept(g_listen_fd, nullptr, nullptr);
    if (c < 0) continue;

    std::string req; char b[4096]; ssize_t n;
    size_t hdr_end = std::string::npos, clen = 0;
    while ((n = recv(c, b, sizeof(b), 0)) > 0) {
      req.append(b, n);
      if (hdr_end == std::string::npos) {
        hdr_end = req.find("\r\n\r\n");
        if (hdr_end != std::string::npos) {
          size_t p = req.find("Content-Length:");
          if (p == std::string::npos) p = req.find("content-length:");
          if (p != std::string::npos) clen = strtoul(req.c_str() + p + 15, nullptr, 10);
        }
      }
      if (hdr_end != std::string::npos && req.size() >= hdr_end + 4 + clen) break;
      if (req.size() > (1u << 20)) break;
    }
    std::string method, path, body;
    if (hdr_end != std::string::npos) {
      size_t sp1 = req.find(' '), sp2 = req.find(' ', sp1 + 1);
      if (sp1 != std::string::npos && sp2 != std::string::npos) {
        method = req.substr(0, sp1); path = req.substr(sp1 + 1, sp2 - sp1 - 1);
      }
      body = req.substr(hdr_end + 4);
    }
    std::string resp = handle_request(method, path, body);
    send(c, resp.data(), resp.size(), MSG_NOSIGNAL);
    close(c);
  }
  close(g_listen_fd); g_listen_fd = -1;
}

/* ------------------------------------------------------------------ */
/* Public API.                                                        */
/* ------------------------------------------------------------------ */
gboolean frame_dump_init(const FrameDumpConfig *cfg) {
  FrameDumpConfig d; memset(&d, 0, sizeof(d));
  if (!cfg) cfg = &d;

  g_enc = nvds_obj_enc_create_context((int) cfg->gpu_id);
  if (!g_enc) { GST_ERROR("frame_dump: nvds_obj_enc_create_context failed"); return FALSE; }

  g_ctrl.enabled.store(cfg->enabled == TRUE);
  g_ctrl.fps.store(cfg->fps ? cfg->fps : 1);
  g_ctrl.quality.store(cfg->quality ? cfg->quality : 80);
  g_ctrl.hour_subfolder.store(cfg->hour_subfolder == TRUE);
  g_ctrl.unique_names.store(cfg->unique_filenames == TRUE);
  g_ctrl.retention_days.store(cfg->retention_days);
  g_ctrl.all_cameras.store(cfg->all_cameras == TRUE);
  {
    std::lock_guard<std::mutex> lk(g_ctrl.cfg_mtx);
    g_ctrl.output_dir = (cfg->default_output_dir && cfg->default_output_dir[0])
                          ? cfg->default_output_dir : "/tmp/frame_dumps";
    /* seed the ON cameras from a ";"-separated list */
    if (cfg->enable_sensors && cfg->enable_sensors[0]) {
      const char *s = cfg->enable_sensors; std::string tok;
      for (; ; ++s) {
        if (*s == ';' || *s == ',' || *s == '\0') {
          /* trim spaces */
          size_t b = tok.find_first_not_of(" \t");
          size_t e = tok.find_last_not_of(" \t");
          if (b != std::string::npos) g_ctrl.on_sensors.insert(tok.substr(b, e - b + 1));
          tok.clear();
          if (*s == '\0') break;
        } else tok += *s;
      }
    }
  }
  mkdir_p(g_ctrl.dir());
  g_bind_addr = (cfg->bind_address && cfg->bind_address[0]) ? cfg->bind_address : "0.0.0.0";

  /* Kafka notifier: seed config and connect if a broker is set (connecting even
   * when disabled lets REST enable it later without a restart). */
  g_kafka.enabled.store(cfg->kafka_enable == TRUE);
  {
    std::lock_guard<std::mutex> lk(g_kafka.mtx);
    g_kafka.broker      = (cfg->kafka_broker      && cfg->kafka_broker[0])      ? cfg->kafka_broker      : "";
    g_kafka.topic       = (cfg->kafka_topic       && cfg->kafka_topic[0])       ? cfg->kafka_topic       : "";
    g_kafka.proto_lib   = (cfg->kafka_proto_lib   && cfg->kafka_proto_lib[0])   ? cfg->kafka_proto_lib   : "";
    g_kafka.config_file = (cfg->kafka_config_file && cfg->kafka_config_file[0]) ? cfg->kafka_config_file : "";
  }
  if (!g_kafka.broker.empty()) kafka_connect();

  g_running.store(true);
  g_writer    = std::thread(writer_loop);
  g_retention = std::thread(retention_loop);

  int port = (cfg->http_port && cfg->http_port[0]) ? atoi(cfg->http_port) : 9857;
  if (port <= 0 || port > 65535) port = 9857;
  g_http = std::thread(http_loop, port);
  return TRUE;
}

/* Read the [frame-dump] group from a GKeyFile (.txt) config. */
static void fd_parse_keyfile(const char *file, FrameDumpConfig &cfg,
                            std::string &loc, std::string &port,
                            std::string &bind, std::string &sensors,
                            std::string &kbroker, std::string &ktopic,
                            std::string &kproto, std::string &kcfg) {
  GKeyFile *kf = g_key_file_new();
  const char *G = "frame-dump";
  if (g_key_file_load_from_file(kf, file, G_KEY_FILE_NONE, nullptr) &&
      g_key_file_has_group(kf, G)) {
    if (g_key_file_has_key(kf, G, "enable", nullptr))
      cfg.enabled = g_key_file_get_boolean(kf, G, "enable", nullptr);
    if (g_key_file_has_key(kf, G, "fps", nullptr))
      cfg.fps = g_key_file_get_integer(kf, G, "fps", nullptr);
    if (g_key_file_has_key(kf, G, "quality", nullptr))
      cfg.quality = g_key_file_get_integer(kf, G, "quality", nullptr);
    if (g_key_file_has_key(kf, G, "gpu-id", nullptr))
      cfg.gpu_id = g_key_file_get_integer(kf, G, "gpu-id", nullptr);
    if (g_key_file_has_key(kf, G, "hour-subfolder", nullptr))
      cfg.hour_subfolder = g_key_file_get_boolean(kf, G, "hour-subfolder", nullptr);
    if (g_key_file_has_key(kf, G, "unique-filenames", nullptr))
      cfg.unique_filenames = g_key_file_get_boolean(kf, G, "unique-filenames", nullptr);
    if (g_key_file_has_key(kf, G, "retention-days", nullptr))
      cfg.retention_days = g_key_file_get_integer(kf, G, "retention-days", nullptr);
    if (g_key_file_has_key(kf, G, "all-cameras", nullptr))
      cfg.all_cameras = g_key_file_get_boolean(kf, G, "all-cameras", nullptr);
    if (g_key_file_has_key(kf, G, "kafka-enable", nullptr))
      cfg.kafka_enable = g_key_file_get_boolean(kf, G, "kafka-enable", nullptr);
    gchar *s;
    if ((s = g_key_file_get_string(kf, G, "location", nullptr)))         { loc = s;     g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "port", nullptr)))             { port = s;    g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "bind-address", nullptr)))     { bind = s;    g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "enable-sensors", nullptr)))   { sensors = s; g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "kafka-broker", nullptr)))     { kbroker = s; g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "kafka-topic", nullptr)))      { ktopic = s;  g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "kafka-proto-lib", nullptr)))  { kproto = s;  g_free(s); }
    if ((s = g_key_file_get_string(kf, G, "kafka-config-file", nullptr))){ kcfg = s;    g_free(s); }
  }
  g_key_file_free(kf);
}

/* Trim surrounding whitespace and matched quotes from a YAML scalar. */
static std::string fd_yaml_strip(std::string s) {
  size_t a = s.find_first_not_of(" \t\r\n");
  if (a == std::string::npos) return "";
  size_t b = s.find_last_not_of(" \t\r\n");
  s = s.substr(a, b - a + 1);
  if (s.size() >= 2 && (s.front() == '"' || s.front() == '\'') && s.back() == s.front())
    s = s.substr(1, s.size() - 2);
  return s;
}
static bool fd_yaml_bool(const std::string &v) {
  return v == "1" || v == "true" || v == "True" || v == "yes" || v == "on";
}

/* Read the flat `frame-dump:` block from a YAML config without yaml-cpp.
 * Only simple "key: value" scalars under the top-level "frame-dump:" mapping
 * are honoured, which covers every frame-dump key (enable-sensors is a
 * single ';'-separated string, same as the .txt form). */
static void fd_parse_yaml(const char *file, FrameDumpConfig &cfg,
                          std::string &loc, std::string &port,
                          std::string &bind, std::string &sensors,
                          std::string &kbroker, std::string &ktopic,
                          std::string &kproto, std::string &kcfg) {
  FILE *fp = fopen(file, "r");
  if (!fp) return;
  char *line = nullptr; size_t cap = 0; ssize_t len;
  bool in_block = false;
  while ((len = getline(&line, &cap, fp)) != -1) {
    std::string raw(line, (size_t)len);
    size_t hash = raw.find('#');                 /* strip trailing comment */
    if (hash != std::string::npos) raw = raw.substr(0, hash);
    if (raw.find_first_not_of(" \t\r\n") == std::string::npos) continue; /* blank */
    bool indented = (raw[0] == ' ' || raw[0] == '\t');
    std::string trimmed = fd_yaml_strip(raw);
    if (!in_block) {
      if (!indented && trimmed.rfind("frame-dump:", 0) == 0) in_block = true;
      continue;
    }
    if (!indented) break;                        /* dedent -> end of block */
    size_t colon = trimmed.find(':');
    if (colon == std::string::npos) continue;
    std::string key = fd_yaml_strip(trimmed.substr(0, colon));
    std::string val = fd_yaml_strip(trimmed.substr(colon + 1));
    if (val.empty()) continue;
    if      (key == "enable")           cfg.enabled          = fd_yaml_bool(val) ? TRUE : FALSE;
    else if (key == "fps")              cfg.fps              = atoi(val.c_str());
    else if (key == "quality")          cfg.quality          = atoi(val.c_str());
    else if (key == "gpu-id")           cfg.gpu_id           = atoi(val.c_str());
    else if (key == "hour-subfolder")   cfg.hour_subfolder   = fd_yaml_bool(val) ? TRUE : FALSE;
    else if (key == "unique-filenames") cfg.unique_filenames = fd_yaml_bool(val) ? TRUE : FALSE;
    else if (key == "retention-days")   cfg.retention_days   = atoi(val.c_str());
    else if (key == "all-cameras")      cfg.all_cameras      = fd_yaml_bool(val) ? TRUE : FALSE;
    else if (key == "kafka-enable")     cfg.kafka_enable     = fd_yaml_bool(val) ? TRUE : FALSE;
    else if (key == "location")         loc     = val;
    else if (key == "port")             port    = val;
    else if (key == "bind-address")     bind    = val;
    else if (key == "enable-sensors")   sensors = val;
    else if (key == "kafka-broker")     kbroker = val;
    else if (key == "kafka-topic")      ktopic  = val;
    else if (key == "kafka-proto-lib")  kproto  = val;
    else if (key == "kafka-config-file") kcfg   = val;
  }
  free(line);
  fclose(fp);
}

gboolean frame_dump_init_from_file(const char *config_file, guint gpu_id) {
  FrameDumpConfig cfg; memset(&cfg, 0, sizeof(cfg));
  cfg.gpu_id = gpu_id;
  std::string loc, port, bind, sensors;   /* hold strings until init copies them */
  std::string kbroker, ktopic, kproto, kcfg;

  if (config_file && config_file[0]) {
    std::string f(config_file);
    auto ends = [&](const char *e) {
      size_t n = strlen(e);
      return f.size() >= n && f.compare(f.size() - n, n, e) == 0;
    };
    if (ends(".yml") || ends(".yaml"))
      fd_parse_yaml(config_file, cfg, loc, port, bind, sensors, kbroker, ktopic, kproto, kcfg);
    else
      fd_parse_keyfile(config_file, cfg, loc, port, bind, sensors, kbroker, ktopic, kproto, kcfg);
  }
  if (!loc.empty())     cfg.default_output_dir = loc.c_str();
  if (!port.empty())    cfg.http_port          = port.c_str();
  if (!bind.empty())    cfg.bind_address       = bind.c_str();
  if (!sensors.empty()) cfg.enable_sensors     = sensors.c_str();
  if (!kbroker.empty()) cfg.kafka_broker       = kbroker.c_str();
  if (!ktopic.empty())  cfg.kafka_topic        = ktopic.c_str();
  if (!kproto.empty())  cfg.kafka_proto_lib    = kproto.c_str();
  if (!kcfg.empty())    cfg.kafka_config_file  = kcfg.c_str();
  return frame_dump_init(&cfg);            /* init copies all values */
}

gboolean frame_dump_attach(GstElement *tracker) {
  if (!tracker) return FALSE;
  GstPad *src = gst_element_get_static_pad(tracker, "src");
  if (!src) { GST_ERROR("frame_dump: no src pad on tracker"); return FALSE; }
  gst_pad_add_probe(src, GST_PAD_PROBE_TYPE_BUFFER, frame_dump_probe, nullptr, nullptr);
  gst_object_unref(src);
  return TRUE;
}

/* One-call wiring: init once (from config), attach every time. */
static std::once_flag g_wire_once;
void frame_dump_wire(const char *config_file, GstElement *tracker) {
  std::call_once(g_wire_once, [config_file] {
    if (frame_dump_init_from_file(config_file, 0)) std::atexit(frame_dump_deinit);
  });
  frame_dump_attach(tracker);
}

void frame_dump_deinit(void) {
  g_running.store(false);
  g_wq_cv.notify_all();
  g_ret_cv.notify_all();
  if (g_http.joinable())      g_http.join();
  if (g_writer.joinable())    g_writer.join();
  if (g_retention.joinable()) g_retention.join();
  if (g_enc) { nvds_obj_enc_destroy_context(g_enc); g_enc = nullptr; }
  /* Kafka: disconnect (flushes) + unload the adaptor. */
  if (g_kafka.handle && g_kafka.disconnect) g_kafka.disconnect(g_kafka.handle);
  g_kafka.handle = nullptr;
  if (g_kafka.lib) { dlclose(g_kafka.lib); g_kafka.lib = nullptr; }
}