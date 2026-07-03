#include "captive_portal.h"
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

namespace icsmesh {

namespace {
WebServer g_server(80);
DNSServer g_dns;
CaptivePortal* g_owner = nullptr;
constexpr uint8_t kDnsPort = 53;

const char* parLabel(ParStatus s) {
  switch (s) {
    case ParStatus::OK: return "OK";
    case ParStatus::EMERGENCY: return "EMERGENCY";
    case ParStatus::MAYDAY: return "MAYDAY";
    case ParStatus::OUT_OF_CONTACT: return "OUT OF CONTACT";
  }
  return "?";
}

String pageShell(const String& title, const String& body) {
  String html;
  html.reserve(body.length() + 512);
  html += "<!DOCTYPE html><html><head><meta charset='utf-8'>";
  html += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>" + title + "</title><style>";
  html += "body{font-family:sans-serif;margin:0;padding:16px;background:#111;color:#eee}";
  html += "a{color:#7fd}nav a{margin-right:12px}";
  html += "button{padding:12px 20px;font-size:16px;margin:4px 0;width:100%;border-radius:6px;border:none}";
  html += ".ok{background:#2a5}.warn{background:#e93}.crit{background:#c33;color:#fff}";
  html += "input,textarea{width:100%;box-sizing:border-box;padding:8px;margin:4px 0;background:#222;color:#eee;border:1px solid #444}";
  html += "table{width:100%;border-collapse:collapse}td,th{border:1px solid #444;padding:6px;text-align:left}";
  html += "</style></head><body>";
  html += "<nav><a href='/'>Status</a><a href='/ics214'>ICS-214</a><a href='/ics205'>ICS-205</a><a href='/ics201'>ICS-201</a></nav><hr>";
  html += body;
  html += "</body></html>";
  return html;
}

void handleRoot() {
  String body = "<h2>" + g_owner->node_label_ + "</h2>";
  body += "<p>Node ID: " + String(g_owner->self_node_id_) + "</p>";
  body += "<p>PAR status: <b>" + String(parLabel(g_owner->current_par_)) + "</b></p>";
  body += "<form method='POST' action='/par'>";
  body += "<button name='status' value='0' class='ok'>OK</button>";
  body += "<button name='status' value='1' class='warn'>EMERGENCY</button>";
  body += "<button name='status' value='2' class='crit'>MAYDAY</button>";
  body += "</form>";
  g_server.send(200, "text/html", pageShell("Status", body));
}

void handlePar() {
  if (g_server.hasArg("status")) {
    auto s = static_cast<ParStatus>(g_server.arg("status").toInt());
    g_owner->current_par_ = s;
    if (g_owner->par_handler_) g_owner->par_handler_(s);
  }
  g_server.sendHeader("Location", "/");
  g_server.send(303);
}

void handleIcs214() {
  Ics214Log log;
  loadIcs214(log);
  String body = "<h2>ICS-214 Activity Log</h2>";
  body += "<form method='POST' action='/ics214/add'>";
  body += "<textarea name='text' rows='2' placeholder='Log entry...'></textarea>";
  body += "<button class='ok'>Add entry</button></form><hr><table><tr><th>Time (ms)</th><th>Node</th><th>Entry</th></tr>";
  for (auto it = log.entries.rbegin(); it != log.entries.rend(); ++it) {
    body += "<tr><td>" + String(it->timestamp_ms) + "</td><td>" + String(it->author_node_id) + "</td><td>" + it->text + "</td></tr>";
  }
  body += "</table>";
  g_server.send(200, "text/html", pageShell("ICS-214", body));
}

void handleIcs214Add() {
  if (g_server.hasArg("text") && g_server.arg("text").length() > 0) {
    String text = g_server.arg("text");
    if (text.length() > kIcs214TextMax - 1) text = text.substring(0, kIcs214TextMax - 1);
    if (g_owner->ics214_handler_) g_owner->ics214_handler_(text);

    Ics214Log log;
    loadIcs214(log);
    log.append(millis(), g_owner->self_node_id_, text);
    saveIcs214(log);
  }
  g_server.sendHeader("Location", "/ics214");
  g_server.send(303);
}

void handleIcs205() {
  Ics205CommsPlan plan;
  bool have = loadIcs205(plan);
  String body = "<h2>ICS-205 Communications Plan</h2>";
  if (!have || plan.channels.empty()) {
    body += "<p><i>No comms plan synced yet from command.</i></p>";
  } else {
    body += "<table><tr><th>Channel</th><th>Freq/Tone</th><th>Assignment</th></tr>";
    for (const auto& c : plan.channels) {
      body += "<tr><td>" + c.name + "</td><td>" + c.freq_or_tone + "</td><td>" + c.assignment + "</td></tr>";
    }
    body += "</table>";
  }
  g_server.send(200, "text/html", pageShell("ICS-205", body));
}

void handleIcs201() {
  Ics201Briefing b;
  bool have = loadIcs201(b);
  String body = "<h2>ICS-201 Incident Briefing</h2>";
  if (!have || b.incident_name.length() == 0) {
    body += "<p><i>No briefing synced yet from command.</i></p>";
  } else {
    body += "<p><b>Incident:</b> " + b.incident_name + "</p>";
    body += "<p><b>IC:</b> " + b.incident_commander + "</p>";
    body += "<p><b>Objectives:</b><br>" + b.objectives + "</p>";
    body += "<p><b>Situation:</b><br>" + b.situation_summary + "</p>";
  }
  g_server.send(200, "text/html", pageShell("ICS-201", body));
}

void handleNotFound() {
  // Any unknown path redirects to / — this plus the DNS wildcard is what
  // makes phones pop the "Sign in to network" captive-portal prompt.
  g_server.sendHeader("Location", "/");
  g_server.send(302);
}
}  // namespace

void CaptivePortal::begin(const String& node_label, uint16_t self_node_id) {
  g_owner = this;
  node_label_ = node_label;
  self_node_id_ = self_node_id;

  String ssid = "ICS-" + node_label;
  WiFi.softAP(ssid.c_str());
  IPAddress ip = WiFi.softAPIP();

  g_dns.start(kDnsPort, "*", ip);  // wildcard DNS -> every hostname resolves to us

  g_server.on("/", handleRoot);
  g_server.on("/par", HTTP_POST, handlePar);
  g_server.on("/ics214", handleIcs214);
  g_server.on("/ics214/add", HTTP_POST, handleIcs214Add);
  g_server.on("/ics205", handleIcs205);
  g_server.on("/ics201", handleIcs201);
  g_server.onNotFound(handleNotFound);
  g_server.begin();

  Serial.println("[portal] AP '" + ssid + "' up at " + ip.toString());
}

void CaptivePortal::handleClient() {
  g_dns.processNextRequest();
  g_server.handleClient();
}

}  // namespace icsmesh
