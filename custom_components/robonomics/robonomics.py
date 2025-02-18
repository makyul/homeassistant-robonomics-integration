from substrateinterface import SubstrateInterface, Keypair, KeypairType
from robonomicsinterface import Account, Subscriber, SubEvent, Datalog, RWS, Datalog
from robonomicsinterface.utils import ipfs_32_bytes_to_qm_hash
from aenum import extend_enum
from homeassistant.core import callback, HomeAssistant
import logging
import typing as tp
import asyncio
import time
import json
from .utils import to_thread

_LOGGER = logging.getLogger(__name__)

class Robonomics:
    def __init__(self,
                hass: HomeAssistant, 
                sub_owner_address: str, 
                sub_admin_seed: str,
                ) -> None:
        self.hass: HomeAssistant = hass
        self.sub_owner_address: str = sub_owner_address
        self.sub_admin_seed: str = sub_admin_seed
        self.sending_states: bool = False
        self.sending_creds: bool = False
        self.on_queue: int = 0
        self.devices_list: tp.List[str] = []
        try:
            extend_enum(
                    SubEvent,
                    "MultiEvent",
                    f"{SubEvent.NewDevices.value, SubEvent.NewLaunch.value, SubEvent.NewRecord.value}",
                )
        except Exception as e:
            _LOGGER.error(f"Exception in enum: {e}")

    async def find_password(self, address: str) -> tp.Optional[str]:
        """
        Look for encrypted password in the datalog of the given account

        :param address: The address of the account

        :return: Encrypted password or None if password wasn't found
        """
        _LOGGER.debug(f"Start look for password for {address}")
        datalog = Datalog(Account())
        for i in range(100):
            try:
                _LOGGER.debug(datalog.get_item(address, i)[1])
                data = json.loads(datalog.get_item(address, i)[1])
                if "admin" in data:
                    if data["subscription"] == self.sub_owner_address:
                        return data["admin"]
            except Exception as e:
                #_LOGGER.error(f"Exception in find password {e}")
                continue
        else:
            return None

    def subscribe(self, handle_launch: tp.Callable, manage_users: tp.Callable, change_password: tp.Callable) -> None:
        """
        Subscribe to NewDevices and NewLaunch events

        :param handle_launch: Call this function if NewLaunch event
        :param manage_users: Call this function if NewDevices event
        :param change_password: Call this function if NewRecord event from one of devices

        """
        self.handle_launch: tp.Callable = handle_launch
        self.manage_users: tp.Callable = manage_users
        self.change_password: tp.Callable = change_password
        try:
            account = Account()
            Subscriber(account, SubEvent.MultiEvent, subscription_handler=self.callback_new_event)
        except Exception as e:
            _LOGGER.debug(f"subscribe exception {e}")

            time.sleep(4)
            self.subscribe(handle_launch, manage_users, change_password)
    
    @callback
    def callback_new_event(self, data: tp.Tuple[tp.Union[str, tp.List[str]]]) -> None:
        """
        Check the event and call handlers

        :param data: Data from event

        """
        # _LOGGER.debug(f"Got Robonomics event: {data}")
        sub_admin = Account(
                seed=self.sub_admin_seed, crypto_type=KeypairType.ED25519
            )
        if type(data[1]) == str and data[1] == sub_admin.get_address():
            self.hass.async_create_task(self.handle_launch(data)) 
        elif type(data[1]) == int and data[0] in self.devices_list:
            self.hass.async_create_task(self.change_password(data))
        elif type(data[1]) == list and data[0] == self.sub_owner_address:
            self.hass.async_create_task(self.manage_users(data))
            #self.hass.states.async_set(f"{DOMAIN}.rws.state", data)
            #print(data)

    @to_thread
    def send_datalog(
        self, data: str, seed: str, subscription: bool
    ) -> str:
        """
        Record datalog

        :param data: Data for Datalog recors
        :param seed: Mnemonic or raw seed for account that will send the transaction
        :param subscription: True if record datalog as RWS call

        :return: Exstrinsic hash

        """

        account = Account(seed=seed, crypto_type=KeypairType.ED25519)
        if subscription:
            try:
                _LOGGER.debug(f"Start creating rws datalog")
                datalog = Datalog(
                    account, rws_sub_owner=self.sub_owner_address
                )
            except Exception as e:
                _LOGGER.error(f"Create datalog class exception: {e}")
        else:
            try:
                _LOGGER.debug(f"Start creating datalog")
                datalog = Datalog(account)
            except Exception as e:
                _LOGGER.error(f"Create datalog class exception: {e}")
        try:    
            receipt = datalog.record(data)
            _LOGGER.debug(f"Datalog created with hash: {receipt}")
            return receipt
        except Exception as e:
            _LOGGER.error(f"send datalog exception: {e}")
            return None

    async def send_datalog_states(self, data: str) -> str:
        """
        Record datalog from sub admin using subscription

        :param data: Data to record

        :return: Exstrinsic hash

        """
        _LOGGER.debug(f"Send datalog states request, another datalog: {self.sending_states}")
        if self.sending_states: 
            _LOGGER.debug("Another datalog is sending. Wait...")
            self.on_queue += 1
            on_queue = self.on_queue
            while self.sending_states:
                await asyncio.sleep(5)
                if on_queue < self.on_queue:
                    _LOGGER.debug("Stop waiting to send datalog")
                    return
            self.sending_states = True
            self.on_queue = 0
            await asyncio.sleep(10)
        else:
            self.sending_states = True
            self.on_queue = 0
        receipt = await self.send_datalog(data, self.sub_admin_seed, True)
        self.sending_states = False
        return receipt

    def get_devices_list(self):
        """
        Return devices list for sub owner account

        :return: List of devices
        """
        try:
            devices_list = RWS(Account()).get_devices(self.sub_owner_address)
            _LOGGER.debug(f"Got devices list: {devices_list}")
            sub_admin = Account(
                seed=self.sub_admin_seed, crypto_type=KeypairType.ED25519
            )
            if devices_list != None:
                devices_list.remove(sub_admin.get_address())
                try:
                    devices_list.remove(self.sub_owner_address)
                except:
                    pass
            self.devices_list = devices_list
            return self.devices_list
        except Exception as e:
            print(f"error while getting rws devices list {e}")
