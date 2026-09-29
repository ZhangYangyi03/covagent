# Coverage run with verilator, using a main that actually flushes coverage.
# --timing is required because the testbench uses @(posedge clk).
RTL := alu.v
TB  := tb_alu.v
TOP := tb_alu
VFLAGS := --cc --timing --coverage --coverage-line --coverage-toggle -Wno-fatal --top-module $(TOP)

all: cov

obj/cfg.h:
	mkdir -p obj
	sed -e "s/@TOP@/$(TOP)/g" cfg.h.in > obj/cfg.h

obj/V$(TOP).h: $(RTL) $(TB)
	verilator $(VFLAGS) $(TB) $(RTL) -Mdir obj

obj/sim: obj/V$(TOP).h obj/cfg.h sim_main.cpp
	verilator $(VFLAGS) $(TB) $(RTL) sim_main.cpp -Mdir obj -o sim --exe
	$(MAKE) -C obj -f V$(TOP).mk
	cd obj && ./sim | tee ../run.log

cov: obj/sim
	verilator_coverage --write-info coverage.info obj/cov.dat

clean:
	rm -rf obj coverage.info run.log annotated cov.dat

.PHONY: all run cov clean
