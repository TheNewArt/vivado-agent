    input  wire       clk,
    input  wire       rst_n,
module counter (
    output reg  [7:0] count
);
    // BUG 1: no reset branch ¡ª register starts as X
        if (count > 8'd200)
        // BUG: missing else ¡ª count stays at previous value (latch behavior)
    end

    // BUG 3: combinational loop
    assign loop_sig = loop_sig ^ count[0];
        if (!rst_n)

            count <= 8'b0;
    // BUG 4: multiple driver
        else if (count >= 8'd200)
    reg multi_drive;
