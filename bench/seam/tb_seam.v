// The single producer/fifo wiring, with exactly one line under test.
//
// Every variant below is this file with the `assign seam_en` line rewritten; the
// two block files are byte-identical everywhere. That is the whole experiment:
// if the coverage report is doing its job, a wrong wire should show up in it.
`timescale 1ns/1ps
module tb_seam;
    reg clk = 0, rst = 1, en = 0, rd_en = 0;
    reg [7:0] wr_data = 0;
    wire [2:0] count;
    wire full, empty;
    wire [7:0] rd_data;
    wire [2:0] prod_cnt;
    wire seam_en;

    counter #(.W(3)) u_prod(.clk(clk), .rst(rst), .en(en), .cnt(prod_cnt));
    sync_fifo #(.WIDTH(8), .DEPTH(4)) u_fifo(
        .clk(clk), .rst(rst), .wr_en(seam_en), .wr_data(wr_data),
        .rd_en(rd_en), .rd_data(rd_data), .full(full), .empty(empty), .count(count));

    assign seam_en = en;          /* SEAM: the wiring under test */

    always #5 clk = ~clk;

    task drive(input e, input [7:0] d, input r);
        begin @(posedge clk); en = e; wr_data = d; rd_en = r; @(posedge clk); en = 0; end
    endtask

    initial begin
        repeat (2) @(posedge clk);
        rst = 0;
        drive(1, 8'd10, 0);
        drive(1, 8'd20, 1);
        drive(1, 8'd30, 0);
        repeat (2) @(posedge clk);
        $display("TB_DONE");
        $finish;
    end
endmodule
