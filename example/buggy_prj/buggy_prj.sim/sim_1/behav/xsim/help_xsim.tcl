log_wave -r /
run 100ns
set sigs [get_objects -r /tb_buggy/u_dut/*]
puts "===SIGNALS==="
foreach s $sigs {
    puts "NAME: [get_property NAME $s]"
    puts "TYPE: [get_property TYPE $s]"
}
puts "===END==="
