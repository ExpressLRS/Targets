import os, sys
import json
import glob
import argparse
import hardware
import re

hadError = False
warnEnabled = False
firmwares = set()
ignored_validations = set()
stm32_targets = [
    'DIY_900_TX_STM32_SX1272',
    'DIY_900_RX_STM32_SX1272',
    'Frsky_TX_R9M',
    'Frsky_TX_R9M_LITE',
    'Frsky_TX_R9M_LITE_PRO',
    'Frsky_RX_R9MM_R9MINI',
    'Frsky_RX_R9SLIM',
    'Frsky_RX_R9SLIMPLUS',
    'Frsky_RX_R9SLIMPLUS_OTA',
    'Frsky_RX_R9MX',
    'DIY_2400_RX_STM32_CCG_Nano_v0_5',
    'HappyModel_TX_ES915TX',
    'HappyModel_RX_ES915RX',
    'HappyModel_PP_2400_RX',
    'GHOST_2400_TX',
    'GHOST_2400_TX_LITE',
    'GHOST_ATTO_2400_RX',
    'Jumper_RX_R900MINI',
    'NamimnoRC_VOYAGER_900_TX',
    'NamimnoRC_VOYAGER_900_RX',
    'NamimnoRC_FLASH_2400_TX',
    'NamimnoRC_FLASH_2400_RX',
    'FM30_TX',
    'FM30_RX_MINI_AS_TX',
    'FM30_RX_MINI'
]


# Format of each entry is the dotted path into targets.json split into a 3-tuple
upload_method_exclusions = {
    ('hdzero', 'tx_2400', 'radio'),
    ('radiomaster', 'tx_dual', 'ax12')
}


def set_ignored_validations(device=None):
    global ignored_validations
    ignored_validations = set() if device is None else set(device.get('validation_ignores', []))


def error(msg, code=None):
    if code is not None and code in ignored_validations:
        return
    global hadError
    hadError = True
    print("ERROR: " + msg)


def warn(msg, code=None):
    if code is not None and code in ignored_validations:
        return
    if warnEnabled:
        global hadError
        hadError = True
        print("WARNING: " + msg)


def parse_version(version):
    m = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    return tuple(int(part) for part in m.groups()) if m else None


def validate_stm32(vendor, type, devname, device):
    for method in device['upload_methods']:
        if method not in ['stlink', 'dfu', 'uart', 'wifi', 'betaflight', 'stock']:
            error(f'Invalid upload method "{method}" for target "{vendor}.{type}.{devname}"', 'invalid_upload_method')
    if 'stlink' not in device['upload_methods']:
        error(f'STM32 based devices must always have "stlink" as an upload_method for target "{vendor}.{type}.{devname}"', 'missing_stlink_upload_method')
    if 'stlink' not in device:
        error(f'STM32 based devices must always have "stlink" attribute for target "{vendor}.{type}.{devname}"', 'missing_stlink_attribute')
    else:
        stlink = device['stlink']
        if 'cpus' not in stlink:
            error(f'The "stlink" attribute for target "{vendor}.{type}.{devname}" must have a list of valid "cpus"', 'missing_stlink_cpus')
        if 'offset' not in stlink:
            error(f'The "stlink" attribute for target "{vendor}.{type}.{devname}" must have a valid "offset"', 'missing_stlink_offset')
        if 'bootloader' not in stlink:
            error(f'The "stlink" attribute for target "{vendor}.{type}.{devname}" must have a valid "bootloader"', 'missing_stlink_bootloader')
    # could check the existence of the bootloader file


def validate_firmware_platform(vendor, type, devname, device):
    if device['platform'] == 'esp32-c3':
        if device['min_version'] < '3.5':
            error(f'device "{vendor}.{type}.{devname}" "min_version" must be at least 3.5.0', 'min_version_too_low_for_esp32_c3')
        if '_ESP32C3_' not in device['firmware']:
            error(f'device "{vendor}.{type}.{devname}" firmware and platform MUST match', 'firmware_platform_mismatch')
    elif device['platform'] == 'esp32-s3':
        if '_ESP32S3_' not in device['firmware']:
            error(f'device "{vendor}.{type}.{devname}" firmware and platform MUST match', 'firmware_platform_mismatch')
    elif device['platform'] == 'esp32':
        if '_ESP32_' not in device['firmware']:
            error(f'device "{vendor}.{type}.{devname}" firmware and platform MUST match', 'firmware_platform_mismatch')
    elif device['platform'] == 'esp8285':
        if '_ESP8285_' not in device['firmware']:
            error(f'device "{vendor}.{type}.{devname}" firmware and platform MUST match', 'firmware_platform_mismatch')


def validate_tx_upload_methods(vendor, type, devname, device, layout):
    if vendor in ['diy', 'generic'] or (vendor, type, devname) in upload_method_exclusions:
        return
    if layout['serial_rx'] == layout['serial_tx']:
        if 'uart' not in device['upload_methods']:
            error(f'device "{vendor}.{type}.{devname}" has shared serial_rx/serial_tx, external modules support "uart" uploads', 'missing_uart_upload_method')
    elif device['platform'].startswith('esp32') and 'etx' not in device['upload_methods']:
        error(f'device "{vendor}.{type}.{devname}" has separate serial_rx/serial_tx, internal modules support "etx" uploads', 'missing_etx_upload_method')


def validate_esp(vendor, type, devname, device):
    if 'lua_name' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "lua_name" child element', 'missing_lua_name')
    if len(device['lua_name']) > 16:
        error(f'device "{vendor}.{type}.{devname}" must have a "lua_name" of 16 characters or less', 'lua_name_too_long')
    # validate layout_file
    if not device['firmware'].startswith('Unified'):
        error(f'ESP target "{vendor}.{type}.{devname}" must be using a Unified firmware', 'non_unified_esp_firmware')
    if 'layout_file' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "layout_file" child element', 'missing_layout_file')
    else:
        dir = ('RX/' if type.startswith('rx') else 'TX/')
        layout_file = device['layout_file']
        if not os.path.isfile(dir + layout_file):
            error(f'File specified by layout_file "{layout_file}" in target "{vendor}.{type}.{devname}", does not exist', 'missing_layout_file_on_disk')
        else:
            # load file, merge overlay, call validate_hardware
            with open(dir + layout_file, 'r') as f:
                global hadError
                layout = json.load(f)
                if 'overlay' in device:
                    layout.update(device['overlay'])
                hadError |= hardware.validate(f'{vendor}.{type}.{devname}', layout, device)
                if type.startswith('tx_'):
                    validate_tx_upload_methods(vendor, type, devname, device, layout)


    # could validate overlay
    if 'prior_target_name' not in device:
        warn(f'device "{vendor}.{type}.{devname}" should have a "prior_target_name" child element', 'missing_prior_target_name')
    validate_firmware_platform(vendor, type, devname, device)


def validate_esp32(vendor, type, devname, device):
    for method in device['upload_methods']:
        if method not in ['uart', 'etx', 'wifi', 'betaflight']:
            error(f'Invalid upload method "{method}" for target "{vendor}.{type}.{devname}"', 'invalid_upload_method')

    validate_esp(vendor, type, devname, device)


def validate_esp8285(vendor, type, devname, device):
    for method in device['upload_methods']:
        if method not in ['uart', 'wifi', 'betaflight']:
            error(f'Invalid upload method "{method}" for target "{vendor}.{type}.{devname}"', 'invalid_upload_method')

    validate_esp(vendor, type, devname, device)

def validate_devices(vendor, type, devname, device):
    set_ignored_validations(device)
    allowed_json_key_characters = re.compile("^[a-z0-9_-]+$")
    if not allowed_json_key_characters.match(devname):
        error(f'device tag "{devname}" can only include lowercase a-z, 0-9, and underscores and dashes', 'invalid_device_tag')

    if 'product_name' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "product_name" child element', 'missing_product_name')
    if 'upload_methods' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "upload_methods" child element', 'missing_upload_methods')
    if 'min_version' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "min_version" child element', 'missing_min_version')
    if 'max_version' in device:
        max_version = parse_version(device['max_version'])
        if max_version is None:
            error(f'device "{vendor}.{type}.{devname}" "max_version" must be a version like "4.0.0"', 'invalid_max_version')
        elif 'min_version' in device and max_version <= parse_version(device['min_version']):
            error(f'device "{vendor}.{type}.{devname}" "max_version" must be greater than "min_version"', 'max_version_not_after_min_version')

    if 'firmware' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "firmware" child element', 'missing_firmware')
    else:
        firmware = device['firmware']
        if firmware in stm32_targets:
            set_ignored_validations()
            return
        if len(firmwares) != 0 and firmware not in firmwares:
            error(f'device "{vendor}.{type}.{devname}" has an invalid firmware file "{firmware}"', 'invalid_firmware_file')
        elif firmware.endswith('_TX') and 'tx_' not in type:
            error(f'device "{vendor}.{type}.{devname}" has an invalid firmware file "{firmware}", it must be a TX target firmware', 'firmware_type_mismatch')
        elif firmware.endswith('_RX') and 'rx_' not in type:
            error(f'device "{vendor}.{type}.{devname}" has an invalid firmware file "{firmware}", it must be an RX target firmware', 'firmware_type_mismatch')

    if 'platform' not in device:
        error(f'device "{vendor}.{type}.{devname}" must have a "platform" child element', 'missing_platform')
    else:
        platform = device['platform']
        if platform == 'stm32':
            validate_stm32(vendor, type, devname, device)
        elif platform == 'esp8285':
            validate_esp8285(vendor, type, devname, device)
        elif platform.startswith('esp32'):
            validate_esp32(vendor, type, devname, device)
        else:
            error(f'invalid platform "{platform}" in device "{vendor}.{type}.{devname}"', 'invalid_platform')

    if 'features' in device:
        for feature in device['features']:
            if feature not in ['buzzer', 'unlock-higher-power', 'fan', 'sbus-uart']:
                error(f'features must contain one or more of [\'buzzer\', \'unlock-higher-power\', \'fan\', \'sbus-uart\'], if present in target "{vendor}.{type}.{devname}"', 'invalid_feature')
    set_ignored_validations()

def validate_vendor(name, types):
    allowed_json_key_characters = re.compile("^[a-z0-9_-]+$")
    if not allowed_json_key_characters.match(name):
        error(f'vendor tag "{name}" can only include lowercase a-z, 0-9, and underscores and dashes')

    if 'name' not in types:
        error(f'vendor "{vendor}" must have a "name" child element')

    for type in types:
        if type not in ['rx_2400', 'rx_900', 'rx_dual', 'tx_2400', 'tx_900', 'tx_dual', 'name']:
            error(f'invalid tag "{type}" in "{vendor}"')
        if type in ['rx_2400', 'rx_900', 'rx_dual', 'tx_2400', 'tx_900', 'tx_dual']:
            for device in types[type]:
                validate_devices(name, type, device, types[type][device])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Configure Binary Firmware")
    parser.add_argument("--warn", "-w", action='store_true', default=False, help="Print warnings")
    args = parser.parse_args()
    warnEnabled = args.warn

    with open('targets.json') as f:
        targets = json.load(f)

        for inifile in glob.iglob('../targets/*.ini'):
            with open(inifile) as ini:
                for line in ini:
                    if line.startswith('[env:'):
                        try:
                            firmware_file = line[5:line.index('_via_')]
                            firmwares.add(firmware_file)
                        except ValueError:
                            print(line)
                            None

        for vendor in targets:
            validate_vendor(vendor, targets[vendor])

        for inifile in glob.iglob('targets/*.ini'):
            with open(inifile) as ini:
                for line in ini:
                    if line.startswith('[env:'):
                        target = line
                    if line.startswith('board_config'):
                        eq = line.find('=')
                        board = line[eq + 1:].strip()
                        parts = board.split('.')
                        if len(parts) != 3:
                            error(f'{inifile}: board_config must have 3 parts')
                        else:
                            vendor = parts[0]
                            type = parts[1]
                            device = parts[2]
                            if vendor not in targets:
                                error(f'{inifile}: targets.json file does not contain vendor {vendor}')
                            elif type not in targets[vendor]:
                                error(f'{inifile}: targets.json "{vendor}" does not contain type {type}')
                            elif device not in targets[vendor][type]:
                                error(f'{inifile}: targets.json "{vendor}.{type}" does not contain device {device}')
    if hadError:
        sys.exit(1)
    sys.exit(0)
