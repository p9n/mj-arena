// C++20 Tag Match (2v2) AI Client for Competition Mahjong Server
// Controls 2 adjacent seats (e.g., Seat 0 & 1 or Seat 2 & 3) over a single
// stdin/stdout JSON Lines pipe using the same shanten/ukeire/yaku heuristics
// as simple_client.cpp. Both seats live in the same process and can share
// state or coordinate tile feeding if desired.

#define MAHJONG_CLIENT_LIBRARY
#include "simple_client.cpp"

namespace mahjong {

class TagClient {
 public:
  explicit TagClient(std::string mode = "normal")
      : mode_(std::move(mode)),
        players_{SimpleClient(mode_), SimpleClient(mode_), SimpleClient(mode_),
                 SimpleClient(mode_)} {}

  void run() {
    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.empty()) continue;
      json msg;
      try {
        msg = json::parse(line);
      } catch (...) {
        continue;
      }

      std::string msg_type = msg.value("type", "");
      if (msg.contains("target_seat") && !msg["target_seat"].is_null()) {
        int target_seat = msg["target_seat"].get<int>();
        if (0 <= target_seat && target_seat < 4) {
          if (msg_type == "match_start" || msg_type == "round_start") {
            controlled_seats_.insert(target_seat);
          }
          json resp = players_[target_seat].handle_message(msg);
          if (!resp.is_null()) {
            resp["seat"] = target_seat;
            std::cout << resp.dump() << "\n" << std::flush;
          }
        }
      } else {
        // Broadcast notification (dora_notify, riichi_accepted, call_notify,
        // round_end, match_end): forward to all controlled seats.
        for (int s : controlled_seats_) {
          players_[s].handle_message(msg);
        }
      }

      if (msg_type == "match_end") {
        break;
      }
    }
  }

 private:
  std::string mode_;
  std::array<SimpleClient, 4> players_;
  std::set<int> controlled_seats_;
};

}  // namespace mahjong

int main(int argc, char** argv) {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);

  std::string mode = "normal";
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      mode = argv[++i];
    } else if (arg.rfind("--mode=", 0) == 0) {
      mode = arg.substr(7);
    }
  }

  mahjong::TagClient client(mode);
  client.run();
  return 0;
}
