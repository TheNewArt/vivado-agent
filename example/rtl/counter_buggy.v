// BUGGY VERSION: missing reset, wrong logic, latch inferred
module counter_buggy (
    input  wire       clk,
    input  wire       rst_n,
    output reg  [7:0] count
);
    // BUG 1: no reset branch — register starts as X
    // BUG 2: combinational always with missing else — latch inferred
    always @(posedge clk) begin
        if (count > 8'd200)
            count <= 8'b0;
        // BUG: missing else — count stays at previous value (latch behavior)
    end

    // BUG 3: combinational loop
    wire loop_sig;
    assign loop_sig = loop_sig ^ count[0];

    // BUG 4: multiple driver
    reg multi_drive;
    always @(posedge clk) multi_drive <= count[0];
    always @(posedge clk) multi_drive <= count[1];
endmodule