// Main Arduino entry point. Hardware, networking and audio live in testable modules.
void fidoSetup();
void fidoLoop();

void setup() { fidoSetup(); }
void loop() { fidoLoop(); }
