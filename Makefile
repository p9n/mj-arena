CXX := g++
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra

TARGETS := client/simple_client client/tag_client

.PHONY: all clean test

all: $(TARGETS)

client/simple_client: client/simple_client.cpp
	$(CXX) $(CXXFLAGS) client/simple_client.cpp -o $@

client/tag_client: client/tag_client.cpp client/simple_client.cpp
	$(CXX) $(CXXFLAGS) client/tag_client.cpp -o $@

test: $(TARGETS)
	python3 -m unittest discover -s tests -v

clean:
	rm -f $(TARGETS)
