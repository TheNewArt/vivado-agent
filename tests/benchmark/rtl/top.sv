// Benchmark design: traffic light controller with intentional bugs
// Tests: dependency graph, FSM detection, CDC detection, latch inference

module top (
    input  wire       clk_100m,
    input  wire       clk_50m,    // secondary clock (CDC)
    input  wire       rst_n,
    input  wire       car_sensor,
    output reg  [2:0] light_main,
    output reg  [2:0] light_side
);

    // State machine without default (BUG: inferred latch)
    reg [1:0] state, next_state;
    parameter IDLE = 2'b00, MAIN_GREEN = 2'b01, SIDE_GREEN = 2'b10, YELLOW = 2'b11;

    always @(posedge clk_100m or negedge rst_n) begin
        if (!rst_n)
            state <= IDLE;
        else
            state <= next_state;
    end

    // Next state logic — missing default (BUG)
    always @(*) begin
        next_state = state;
        case (state)
            IDLE:       next_state = MAIN_GREEN;
            MAIN_GREEN: next_state = car_sensor ? YELLOW : MAIN_GREEN;
            YELLOW:     next_state = SIDE_GREEN;
            // BUG: no case for SIDE_GREEN — latch inferred
        endcase
    end

    // Output logic — combinational, missing else (BUG)
    always @(*) begin
        light_main = 3'b001;  // red
        light_side = 3'b001;
        if (state == MAIN_GREEN)
            light_main = 3'b010;  // green
        else if (state == SIDE_GREEN)
            light_side = 3'b010;
        // BUG: no else for YELLOW — lights stay red
    end

    // CDC: signal crossing from clk_50m to clk_100m without sync (BUG)
    reg car_sensor_sync;
    always @(posedge clk_100m) begin
        car_sensor_sync <= car_sensor;  // BUG: no 2-FF synchronizer
    end

    // Combinational loop (BUG)
    wire loop_signal;
    assign loop_signal = loop_signal ^ car_sensor;

    // Multiple driver (BUG)
    reg multi_drive;
    always @(posedge clk_100m) multi_drive <= car_sensor;
    always @(posedge clk_100m) multi_drive <= car_sensor_sync;

    // Instantiate submodule
    counter #(.WIDTH(8)) tick_counter (
        .clk(clk_100m),
        .rst_n(rst_n),
        .count()
    );

endmodule