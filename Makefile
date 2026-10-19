CXX := g++
CXXFLAGS := -std=c++20 -O2 -s -Wall -Wextra

TARGETS := client/simple_client client/tag_client client/tag_baseline

.PHONY: all clean test

all: $(TARGETS)

client/simple_client: client/simple_client.cpp
	$(CXX) $(CXXFLAGS) client/simple_client.cpp -o $@

client/tag_client: client/tag_client.cpp client/simple_client.cpp
	$(CXX) $(CXXFLAGS) client/tag_client.cpp -o $@

client/tag_baseline: client/tag_baseline.cpp
	$(CXX) $(CXXFLAGS) client/tag_baseline.cpp -o $@

test: $(TARGETS)
	python3 -m unittest discover -s tests -v

clean:
	rm -f $(TARGETS)
