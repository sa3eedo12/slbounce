
ARCH 		:= aarch64
CROSS_COMPILE 	:= aarch64-linux-gnu-

AS		:= $(CROSS_COMPILE)as
CC		:= $(CROSS_COMPILE)gcc
LD		:= $(CROSS_COMPILE)ld
OBJCOPY		:= $(CROSS_COMPILE)objcopy

DTC		:= dtc

OUT_DIR		:= $(CURDIR)/out

GNUEFI_DIR	= $(CURDIR)/external/gnu-efi
GNUEFI_OUT	= $(GNUEFI_DIR)/$(ARCH)

LIBFDT_INC	= $(CURDIR)/external/dtc/libfdt/
SYSREG_INC	= $(CURDIR)/external/arm64-sysreg-lib/include

SUBSYSTEM_APP 	= 10	# EFI Application
SUBSYSTEM_RT 	= 12	# EFI Runtime driver

CFLAGS += \
	-I$(GNUEFI_DIR)/inc/ -I$(GNUEFI_DIR)/inc/$(ARCH) -I$(GNUEFI_DIR)/inc/protocol \
	-I$(SYSREG_INC) -I$(LIBFDT_INC) -Isrc/include \
	-fpic -fshort-wchar -fno-stack-protector -ffreestanding \
	-DCONFIG_$(ARCH) -D__MAKEWITH_GNUEFI -DGNU_EFI_USE_MS_ABI \
	-mstrict-align

ifneq ($(DEBUG),)
	CFLAGS  += -DEFI_DEBUG
endif

# Surface Pro 11 fork, both default off (docs/SP11-Q1N1-BOOT-STUDY.md sections 4.5 and 6): the
# scoped/attribute-filtered/chunked ExitBootServices dcache sweep, and the EL2 de-VHE conversion in
# tb_longjmp's restore. Neither changes a single byte of the default build; see
# tools/test-sp11-scoped-sweep.py's DefaultOff class for the proof.
ifneq ($(SLBOUNCE_SCOPED_SWEEP),)
	CFLAGS += -DSLBOUNCE_SCOPED_SWEEP
endif
ifneq ($(SLBOUNCE_SP11_DEVHE),)
	CFLAGS += -DSLBOUNCE_SP11_DEVHE
endif

ifneq ($(SLBOUNCE_ALWAYS_SWITCH),)
	CFLAGS  += -DSLBOUNCE_ALWAYS_SWITCH
endif

LDFLAGS += \
	-Wl,--no-wchar-size-warning \
	-e efi_main \
	-s -Wl,-Bsymbolic -nostdlib -shared \
	-L $(GNUEFI_OUT)/lib/ -L $(GNUEFI_OUT)/gnuefi \

LIBS = \
	-lefi -lgnuefi \
	-T $(GNUEFI_DIR)/gnuefi/elf_$(ARCH)_efi.lds

LIBEFI_A 	:= $(GNUEFI_OUT)/lib/libefi.a
LIBGNUEFI_A 	:= $(GNUEFI_OUT)/gnuefi/libgnuefi.a
CRT0_O 		:= $(GNUEFI_OUT)/gnuefi/crt0-efi-$(ARCH).o

LIBFDT_OBJS := \
	$(OUT_DIR)/external/dtc/libfdt/fdt.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_ro.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_wip.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_sw.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_rw.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_empty_tree.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_addresses.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_check.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_overlay.o \
	$(OUT_DIR)/external/dtc/libfdt/fdt_strerror.o \

DTBHACK_LDFLAGS := \
	-Wl,--defsym=EFI_SUBSYSTEM=$(SUBSYSTEM_APP)

DTBHACK_OBJS := \
	$(OUT_DIR)/src/dtbhack_main.o \
	$(OUT_DIR)/src/util.o \
	$(OUT_DIR)/src/arch.o \
	$(OUT_DIR)/src/libc.o \
	$(LIBFDT_OBJS)

SLTEST_LDFLAGS := \
	-Wl,--defsym=EFI_SUBSYSTEM=$(SUBSYSTEM_APP)

SLTEST_OBJS := \
	$(OUT_DIR)/src/test_main.o \
	$(OUT_DIR)/src/util.o \
	$(OUT_DIR)/src/arch.o \
	$(OUT_DIR)/src/sl.o \
	$(OUT_DIR)/src/trans.o \

SLBOUNCE_LDFLAGS := \
	-Wl,--defsym=EFI_SUBSYSTEM=$(SUBSYSTEM_RT)

SLBOUNCE_OBJS := \
	$(OUT_DIR)/src/bounce_main.o \
	$(OUT_DIR)/src/util.o \
	$(OUT_DIR)/src/arch.o \
	$(OUT_DIR)/src/sl.o \
	$(OUT_DIR)/src/trans.o \
	$(OUT_DIR)/src/libc.o \
	$(LIBFDT_OBJS)

# Default off: this object (the scoped-sweep validator) is linked in only with
# SLBOUNCE_SCOPED_SWEEP=1, never otherwise.
ifneq ($(SLBOUNCE_SCOPED_SWEEP),)
SLBOUNCE_OBJS += $(OUT_DIR)/src/sp11_scoped_sweep.o
endif

# Default off: the TCR_EL2 conversion trans.s calls under SLBOUNCE_SP11_DEVHE (sp11_devhe_convert.h)
# is linked in only with SLBOUNCE_SP11_DEVHE=1; without it trans.o's own SP11_DEVHE branch, and
# hence any reference to this symbol, does not exist either (trans.s's own .ifdef), so an SP11_DEVHE
# build without SLBOUNCE_SCOPED_SWEEP is still valid, but never the reverse omission of this object.
# Its own build rule (the -O2 override) is below, after `all`, so it is not mistaken for this
# Makefile's default goal.
ifneq ($(SLBOUNCE_SP11_DEVHE),)
SLBOUNCE_OBJS += $(OUT_DIR)/src/sp11_devhe_convert.o
endif


# trans.s carries the EL2 de-VHE conversion (docs/SP11-Q1N1-BOOT-STUDY.md section 6) only under
# SLBOUNCE_SP11_DEVHE, selected with GNU as's own .ifdef/.else/.endif (trans.s is never run through
# the C preprocessor: the plain %.o: %.s rule below passes it to $(AS) directly, both with and
# without this gate, so the only difference this makes to that rule's own command line is the one
# --defsym below, never present at all with the gate off).
ifneq ($(SLBOUNCE_SP11_DEVHE),)
ASFLAGS += --defsym SLBOUNCE_SP11_DEVHE=1
endif


DTBS := \
	$(OUT_DIR)/dtbo/sc7180-symbols.dtbo \
	$(OUT_DIR)/dtbo/sc7180-el2.dtbo \
	$(OUT_DIR)/dtbo/sc8280xp-symbols.dtbo \
	$(OUT_DIR)/dtbo/sc8280xp-el2.dtbo \
	$(OUT_DIR)/dtbo/x1e-el2.dtbo \

all: $(OUT_DIR)/sltest.efi $(OUT_DIR)/slbounce.efi $(OUT_DIR)/dtbhack.efi

.INTERMEDIATE: $(GNUEFI_DIR)/inc/elf.h
$(GNUEFI_DIR)/inc/elf.h:
	ln -sf /usr/include/elf.h $(GNUEFI_DIR)/inc/elf.h

$(LIBEFI_A): $(GNUEFI_DIR)/inc/elf.h
	@echo [ DEP ] $@
	@$(MAKE) -C$(GNUEFI_DIR) CROSS_COMPILE=$(CROSS_COMPILE) ARCH=$(ARCH) lib

$(LIBGNUEFI_A): $(GNUEFI_DIR)/inc/elf.h
	@echo [ DEP ] $@
	@$(MAKE) -C$(GNUEFI_DIR) CROSS_COMPILE=$(CROSS_COMPILE) ARCH=$(ARCH) gnuefi

$(OUT_DIR)/sltest.so: $(SLTEST_OBJS) $(LIBEFI_A) $(LIBGNUEFI_A)
	@echo [ LD  ] $$(basename $@)
	@$(CC) $(SLTEST_LDFLAGS) $(LDFLAGS) $(CRT0_O) $(SLTEST_OBJS) -o $@ $(LIBS)

$(OUT_DIR)/slbounce.so: $(SLBOUNCE_OBJS) $(LIBEFI_A) $(LIBGNUEFI_A)
	@echo [ LD  ] $$(basename $@)
	@$(CC) $(SLBOUNCE_LDFLAGS) $(LDFLAGS) $(CRT0_O) $(SLBOUNCE_OBJS) -o $@ $(LIBS)

$(OUT_DIR)/dtbhack.so: $(DTBHACK_OBJS) $(LIBEFI_A) $(LIBGNUEFI_A)
	@echo [ LD  ] $$(basename $@)
	@$(CC) $(DTBHACK_LDFLAGS) $(LDFLAGS) $(CRT0_O) $(DTBHACK_OBJS) -o $@ $(LIBS)

$(OUT_DIR)/%.efi: $(OUT_DIR)/%.so
	@echo [ CPY ] $$(basename $@)
	@$(OBJCOPY) -j .text -j .sdata -j .data -j .dynamic -j .dynsym -j .rel* \
	            -j .rela* -j .reloc -j .eh_frame -O binary $< $@

$(OUT_DIR)/%.o: %.c
	@echo [ CC  ] $$(basename $@)
	@mkdir -p $(dir $@)
	@$(CC) $(CFLAGS) -c $< -o $@

# Default off: -O2 overrides the plain $(CFLAGS) every other object above uses (CFLAGS carries no
# -O at all, so plain $(CC) $(CFLAGS) -c defaults to -O0). trans.s calls this function with the MMU
# off, between a TLBI and the SCTLR_EL2 write that turns the MMU back on, where no load or store may
# occur other than through the registers this sequence already owns (docs/SP11-EL2-ENTRY.md section
# 2.3). An -O0 build of this one function spills to the stack and touches x20/x21 (confirmed by
# disassembly while developing this patch); tools/test-sp11-devhe-convert.py's StackSafety class
# compiles it with this exact rule's own flags and fails if that is ever true again. Placed after
# the generic %.o: %.c rule (and after `all`, this Makefile's first target) so it is never mistaken
# for this Makefile's default goal.
ifneq ($(SLBOUNCE_SP11_DEVHE),)
$(OUT_DIR)/src/sp11_devhe_convert.o: src/sp11_devhe_convert.c src/sp11_devhe_convert.h
	@echo [ CC  ] $$(basename $@)
	@mkdir -p $(dir $@)
	@$(CC) $(CFLAGS) -O2 -c $< -o $@
endif

$(OUT_DIR)/%.o: %.s
	@echo [ ASM ] $$(basename $@)
	@mkdir -p $(dir $@)
	@$(AS) $(ASFLAGS) -c $< -o $@

dtbs: $(DTBS)

$(OUT_DIR)/%.dtbo: %.dtso
	@echo [ DTC ] $$(basename $@)
	@mkdir -p $(dir $@)
	@$(DTC) -O dtb -I dts --align 16 -o $@ $<

.PHONY: clean
clean:
	rm -rf $(OUT_DIR)
	$(MAKE) -C$(GNUEFI_DIR) ARCH=$(ARCH) clean

