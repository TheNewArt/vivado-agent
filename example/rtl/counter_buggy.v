
    // BUG 3: combinational loop
    assign loop_sig = loop_sig ^ count[0];
        if (!rst_n)

            count <= 8'b0;
    // BUG 4: multiple driver
