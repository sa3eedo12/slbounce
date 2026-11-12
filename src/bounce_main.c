// SPDX-License-Identifier: BSD-3-Clause
/* Copyright (c) 2024 Nikita Travkin <nikita@trvn.ru> */

#include <stdint.h>

#include <efi.h>
#include <efilib.h>
#include <efidebug.h>

#include <libfdt.h>
#include <string.h>

#include <sysreg/currentel.h>

#include "util.h"
#include "arch.h"
#include "sl.h"

#ifdef SLBOUNCE_SCOPED_SWEEP
#include "sp11-ebs-ranges.h"
#include "sp11_scoped_sweep.h"
#endif

#ifdef SLBOUNCE_SP11_MARKERS
#include "sp11_marker_surface.h"
#endif

/**
 * sl_is_allowed_by_fdt() - Check if dtb is configured for el2.
 *
 * Check if the currently loaded dtb has zap shader explicitly
 * disabled, which is a common enough heuristic for WoA devices
 * as the zap register is otherwise protected.
 *
 * Returns:
 *  - EFI_SUCCESS     if the dtb is usable in EL2.
 *  - EFI_UNSUPPORTED if the dtb is not usable in EL2.
 */
EFI_STATUS sl_is_allowed_by_fdt(void)
{
	EFI_GUID EfiDtbTableGuid = EFI_DTB_TABLE_GUID;
	const char *prop_status;
	EFI_STATUS status;
	int ret, offset;
	void *dtb;

#ifdef SLBOUNCE_ALWAYS_SWITCH
	return EFI_SUCCESS;
#endif

	status = LibGetSystemConfigurationTable(&EfiDtbTableGuid, &dtb);
	if (EFI_ERROR(status))
		return EFI_UNSUPPORTED;

	ret = fdt_check_header(dtb);
	if (ret)
		return EFI_UNSUPPORTED;

	offset = fdt_node_offset_by_compatible(dtb, 0, "qcom,adreno");
	if (offset <= 0)
		return EFI_UNSUPPORTED;

	offset = fdt_subnode_offset(dtb, offset, "zap-shader");
	if (offset <= 0)
		return EFI_UNSUPPORTED;

	prop_status = fdt_getprop(dtb, offset, "status", &ret);
	if (!prop_status || ret <= 0)
		return EFI_UNSUPPORTED;

	if (!strncmp(prop_status, "disabled", ret))
		return EFI_SUCCESS;

	return EFI_UNSUPPORTED;

}

static struct sl_smc_params *smc_data;
static uint64_t pe_data, pe_size, arg_data, arg_size;

EFI_EXIT_BOOT_SERVICES real_ExitBootServices;
EFI_GET_MEMORY_MAP real_GetMemoryMap;

UINTN LastMemoryMapSize = 0;
UINTN LastDescriptorSize = 0;
EFI_MEMORY_DESCRIPTOR *LastMemoryMap;

EFI_STATUS sl_GetMemoryMap(UINTN *MemoryMapSize, EFI_MEMORY_DESCRIPTOR *MemoryMap, UINTN *MapKey,
			   UINTN *DescriptorSize, UINT32 *DescriptorVersion)
{
	EFI_STATUS status = uefi_call_wrapper(real_GetMemoryMap, 5,
			MemoryMapSize, MemoryMap, MapKey, DescriptorSize, DescriptorVersion);

	if (MemoryMapSize)
		LastMemoryMapSize = *MemoryMapSize;
	if (DescriptorSize)
		LastDescriptorSize = *DescriptorSize;
	LastMemoryMap = MemoryMap;

	return status;
}

#ifdef SLBOUNCE_SCOPED_SWEEP
/* A bound on the memory map this function can represent, not a measurement of any real map's size:
 * a map larger than this is refused (EFI_BUFFER_TOO_SMALL) rather than silently read in part. */
#define SP11_SWEEP_MAX_DESCRIPTORS 512u
/* A bound on chunks per declared range; refused (SP11_SWEEP_ERR_TOO_MANY_OPS, by sp11_sweep_range
 * itself) rather than silently truncated. 256 chunks at the smallest sane chunk_cap q1n1 is
 * expected to choose (64 KiB) already covers a 16 MiB range, the largest single range q1n1's own
 * declared list uses (the stage slot, main.c STAGE_BYTES). */
#define SP11_SWEEP_MAX_OPS 256u

static const EFI_GUID sp11_ebs_ranges_guid = SP11_EBS_RANGES_GUID_INIT;
static const CHAR16 sp11_ebs_ranges_variable[] = SP11_EBS_RANGES_VARIABLE_NAME_U16;

/*
 * Reads and validates the declared-ranges table q1n1 (SNAP_PLATFORM_SP11) wrote to a volatile UEFI
 * variable before calling BS->ExitBootServices (docs/SP11-Q1N1-BOOT-STUDY.md section 4.4/7.2),
 * converts the memory map sl_GetMemoryMap already captured into the abstract descriptor shape
 * sp11_scoped_sweep.h needs, then validates and flushes every declared range in turn.
 *
 * Returns EFI_SUCCESS only if every declared range was individually accepted and swept. Any
 * failure -- an absent, foreign or malformed table; more descriptors than this function can
 * represent; or any one declared range refused (not contained in exactly one descriptor, a
 * non-swept type, or not write-back) -- returns an error and flushes nothing at all for the range
 * that failed or any range after it: a partially-honoured contract is exactly the kind of guess
 * this design exists to avoid (section 4.4 point 2, "containment, not blind extent"). The caller
 * (sl_ExitBootServices) must treat any non-success here as a reason to refuse ExitBootServices
 * entirely, not fall back to the blanket sweep this build no longer contains.
 */
static EFI_STATUS sp11_run_scoped_sweep(void)
{
	struct sp11_ebs_ranges_table table;
	UINTN size = sizeof(table);
	UINT32 attributes = 0;

	EFI_STATUS status = uefi_call_wrapper(RT->GetVariable, 5, (CHAR16 *)sp11_ebs_ranges_variable,
			(EFI_GUID *)&sp11_ebs_ranges_guid, &attributes, &size, &table);
	if (EFI_ERROR(status) || size != sizeof(table) || attributes != EFI_VARIABLE_BOOTSERVICE_ACCESS)
		return EFI_NOT_FOUND;
	if (!sp11_ebs_ranges_valid(&table))
		return EFI_COMPROMISED_DATA;

	static struct sp11_sweep_descriptor descs[SP11_SWEEP_MAX_DESCRIPTORS];
	UINTN total = LastDescriptorSize ? LastMemoryMapSize / LastDescriptorSize : 0;
	if (total > SP11_SWEEP_MAX_DESCRIPTORS)
		return EFI_BUFFER_TOO_SMALL;
	for (UINTN i = 0; i < total; i++) {
		EFI_MEMORY_DESCRIPTOR *e = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)LastMemoryMap + LastDescriptorSize * i);
		descs[i].type = e->Type;
		descs[i].attribute = e->Attribute;
		descs[i].base = e->PhysicalStart;
		descs[i].pages = e->NumberOfPages;
	}

	for (uint32_t r = 0; r < table.count; r++) {
		struct sp11_sweep_op ops[SP11_SWEEP_MAX_OPS];
		uint32_t n_ops = 0;
		int rc = sp11_sweep_range(descs, (uint32_t)total, table.range[r].base, table.range[r].size,
				table.chunk_cap, ops, SP11_SWEEP_MAX_OPS, &n_ops);
		if (rc != SP11_SWEEP_OK)
			return EFI_ABORTED;
		for (uint32_t i = 0; i < n_ops; i++)
			clear_dcache_range(ops[i].base, ops[i].size);
	}
	return EFI_SUCCESS;
}
#endif /* SLBOUNCE_SCOPED_SWEEP */

#ifdef SLBOUNCE_SP11_MARKERS
/*
 * Arms the de-VHE trampoline's progress markers (sp11_marker_surface.h): finds the GOP
 * framebuffer, checks it against the memory map q1n1 just fetched, draws slot 0 to say so, and hands
 * the strip's address and stride to trans.s through two extra words of tb_jmp_buf. Any doubt leaves
 * both words zero, and the trampoline then draws nothing.
 *
 * This runs between q1n1's last GetMemoryMap and the real ExitBootServices, so it must not allocate:
 * any allocation changes the map and invalidates the MapKey q1n1 passed. LocateProtocol does not
 * allocate; Print, LibLocateProtocol and LibLocateHandle do, so none of them is used here. The cost
 * is that a refusal is silent; slot 0 not appearing is how it shows.
 */
static void sp11_arm_progress_markers(void)
{
	EFI_GUID gop_guid = EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID;
	EFI_GRAPHICS_OUTPUT_PROTOCOL *gop = NULL;
	struct sp11_marker_strip strip;
	struct sp11_marker_fb fb;

	tb_jmp_buf[21] = 0;
	tb_jmp_buf[22] = 0;

	if (EFI_ERROR(uefi_call_wrapper(BS->LocateProtocol, 3, &gop_guid, NULL, (VOID **)&gop)))
		return;
	if (!gop || !gop->Mode || !gop->Mode->Info)
		return;

	fb.base = gop->Mode->FrameBufferBase;
	fb.size_bytes = gop->Mode->FrameBufferSize;
	fb.width = gop->Mode->Info->HorizontalResolution;
	fb.height = gop->Mode->Info->VerticalResolution;
	fb.pixels_per_scanline = gop->Mode->Info->PixelsPerScanLine;
	fb.pixel_format = gop->Mode->Info->PixelFormat;

	if (sp11_marker_plan((const uint8_t *)LastMemoryMap, LastMemoryMapSize, LastDescriptorSize, &fb, &strip) !=
	    SP11_MARKER_OK)
		return;

	volatile uint32_t *row = (volatile uint32_t *)(uintptr_t)strip.origin;
	for (uint32_t y = 0; y < SP11_MARKER_SIDE_PX; y++) {
		for (uint32_t x = 0; x < SP11_MARKER_SIDE_PX; x++)
			row[x] = 0xffffffffu;
		row = (volatile uint32_t *)((uintptr_t)row + strip.stride_bytes);
	}
	__asm__ volatile("dsb sy" ::: "memory");

	tb_jmp_buf[21] = strip.origin;
	tb_jmp_buf[22] = strip.stride_bytes;
}
#endif /* SLBOUNCE_SP11_MARKERS */

EFI_STATUS sl_ExitBootServices(EFI_HANDLE ImageHandle, UINTN MapKey)
{
	uint64_t smcret = 0;

	if (sl_is_allowed_by_fdt() != EFI_SUCCESS)
		return uefi_call_wrapper(real_ExitBootServices, 2, ImageHandle, MapKey);

	/*
	 * Unfortunately switching to EL2 will corrupt the caches and
	 * the memory will be gone if it was not flushed to ram. Since
	 * the OS loaders generally assume EBS won't break caches, we
	 * have to flush and invalidate everything to make sure loader
	 * doesn't break.
	 *
	 * We can't possibly know which memory was touched by the loader
	 * so we just flush everything that was allocated here. This
	 * is suboptimal but would hopefully make sure we don't crash.
	 *
	 * Note that if we try to flush caches on hyp-owned memory, we
	 * will also crash. Thus we perform AUTH command after we flushed
	 * all the cache.
	 */

#ifdef SLBOUNCE_SCOPED_SWEEP
	/* Scoped, attribute-filtered, chunked replacement for the blanket walk below
	 * (docs/SP11-Q1N1-BOOT-STUDY.md section 4.4): sweep only what the payload itself declared,
	 * inside a descriptor of a swept Type that also advertises EFI_MEMORY_WB, in bounded chunks.
	 * Any refusal here means ExitBootServices itself must fail (fail closed, not a fallback to
	 * the blanket sweep this build does not contain). */
	if (EFI_ERROR(sp11_run_scoped_sweep()))
		return EFI_ABORTED;
#else
	EFI_MEMORY_DESCRIPTOR *MemoryEntry;
	for (int i = 0; i < LastMemoryMapSize / LastDescriptorSize; ++i) {
		MemoryEntry = (EFI_MEMORY_DESCRIPTOR*)((UINT8*)LastMemoryMap + LastDescriptorSize * i);
		uint64_t start = MemoryEntry->PhysicalStart;
		uint64_t size  = MemoryEntry->NumberOfPages * 4096;

		switch (MemoryEntry->Type) {
		case EfiLoaderCode:
		case EfiLoaderData:
		case EfiBootServicesCode:
		case EfiBootServicesData:
		case EfiRuntimeServicesCode:
		case EfiRuntimeServicesData:
		case EfiACPIReclaimMemory:
			clear_dcache_range(start, size);
			break;
		}
	}
#endif

#ifdef SLBOUNCE_SP11_MARKERS
	sp11_arm_progress_markers();
#endif

	EFI_STATUS status = uefi_call_wrapper(real_ExitBootServices, 2, ImageHandle, MapKey);
	if (EFI_ERROR(status))
		return status;

	smcret = sl_smc(smc_data, SL_CMD_AUTH, pe_data, pe_size, arg_data, arg_size);
	if (smcret)
		psci_reboot();

	/* We set a special longjmp point here in hopes SL gets us back. */
	if (tb_setjmp(tb_jmp_buf) == 0) {
		clear_dcache_range((uint64_t)tb_jmp_buf, 8*TB_JMP_BUF_QWORDS);
		smcret = sl_smc(smc_data, SL_CMD_LAUNCH, pe_data, pe_size, arg_data, arg_size);
		if (smcret)
			psci_reboot(); /* Indicate a fatal error with a reboot. */
	}

	return status;
}

EFI_STATUS sl_install(EFI_FILE_HANDLE tcblaunch)
{
	EFI_STATUS ret = EFI_SUCCESS;
	uint64_t smcret = 0;

	ret = sl_create_data(tcblaunch, &smc_data, &pe_data, &pe_size, &arg_data, &arg_size);
	if (EFI_ERROR(ret)) {
		Print(L"Failed to prepare data for Secure-Launch: %d\n", ret);
		return ret;
	}

	Dbg(L"Data creation is done. Trying to prepare Secure-Launch...\n");

	Dbg(L" == Available: ");
	smcret = sl_smc(smc_data, SL_CMD_IS_AVAILABLE, pe_data, pe_size, arg_data, arg_size);
	Dbg(L"0x%x\n", smcret);
	if (smcret) {
		Print(L"This device does not support Secure-Launch.\n");
		return EFI_UNSUPPORTED;
	}

	/*
	 * We can't just install a handler for EBS signal since
	 * we're not guaranteed to be the last code to run.Thus
	 * we install our hook into EBS to run SL right after
	 * the real ExitBootServices() returns.
	 *
	 * Since we also need to know the final memory map, we
	 * hook into GetMemoryMap as well.
	 */
	real_ExitBootServices = BS->ExitBootServices;
	BS->ExitBootServices = sl_ExitBootServices;

	real_GetMemoryMap = BS->GetMemoryMap;
	BS->GetMemoryMap = sl_GetMemoryMap;

	return EFI_SUCCESS;
}


EFI_STATUS efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
	EFI_FILE_HANDLE volume, file;
	CHAR16 **argv;
	INTN argc;
	EFI_STATUS ret;

	InitializeLib(ImageHandle, SystemTable);
	argc = GetShellArgcArgv(ImageHandle, &argv);

	Dbg(L"SL-Bounce\n");
	Dbg(L"Running in EL=%d\n", read_currentel().el);

	if (read_currentel().el != 1) {
		Print(L"Already in EL2!\n\n");
		return EFI_UNSUPPORTED;
	}

	volume = GetVolume(ImageHandle);
	if (!volume) {
		Print(L"Getting volume failed.\n");
		return EFI_INVALID_PARAMETER;
	}

	file = FileOpen(volume, L"tcblaunch.exe");
	if (!file) {
		Print(L"Opening file \"tcblaunch.exe\" failed.\n");
		return EFI_INVALID_PARAMETER;
	}

	ret = sl_install(file);
	if (EFI_ERROR(ret)) {
		Print(L"Installing SL hook failed with %d\n", ret);
		return ret;
	}

	Print(L"===[ slbounce ]==================================\n");
	Print(L" BS->ExitBootServices() was replaced with a hook\n");
	Print(L"  that would perform Secure-Launch right after\n");
	Print(L"exiting UEFI. Your system will crash if SL fails.\n");
	Print(L"=================================================\n");

	return ret;
}
