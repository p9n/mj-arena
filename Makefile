CXX := g++
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra

TARGET := client/simple_client
SRC := client/simple_client.cpp

.PHONY: all clean test

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) $< -o $@

test: $(TARGET)
	python3 -m unittest discover -s tests -v

clean:
	rm -f $(TARGET)
