module debouncer (
    input  wire       clk,
    input  wire       noisy,
    output reg        clean
);
    reg [2:0] shift;
    always @(posedge clk) begin
        shift <= {shift[1:0], noisy};
        clean <= &shift;
    end
endmodule