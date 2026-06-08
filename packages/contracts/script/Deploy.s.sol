// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/StakeVault.sol";
import "../src/ReputationManager.sol";
import "../src/DecisionLedger.sol";
import "../src/AgentRegistry.sol";
import "../src/QueryEscrow.sol";
import "../src/ProposalEscrow.sol";
import "../src/FreelanceEscrow.sol";

contract DeployScript is Script {
    function run() external {
        // Accept both PRIVATE_KEY and DEPLOYER_PRIVATE_KEY
        uint256 deployerPrivateKey;
        try vm.envUint("PRIVATE_KEY") returns (uint256 k) {
            deployerPrivateKey = k;
        } catch {
            deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        }
        address deployer = vm.addr(deployerPrivateKey);

        vm.startBroadcast(deployerPrivateKey);

        // 1. StakeVault
        StakeVault stakeVault = new StakeVault();
        console.log("StakeVault:", address(stakeVault));

        // 2. ReputationManager
        ReputationManager reputationManager = new ReputationManager();
        console.log("ReputationManager:", address(reputationManager));

        // 3. DecisionLedger
        DecisionLedger decisionLedger = new DecisionLedger();
        console.log("DecisionLedger:", address(decisionLedger));

        // 4. AgentRegistry
        AgentRegistry agentRegistry = new AgentRegistry(
            address(stakeVault),
            address(reputationManager)
        );
        console.log("AgentRegistry:", address(agentRegistry));

        // 5. QueryEscrow
        QueryEscrow queryEscrow = new QueryEscrow(
            address(agentRegistry),
            address(reputationManager),
            address(decisionLedger)
        );
        console.log("QueryEscrow:", address(queryEscrow));

        // 6. ProposalEscrow
        ProposalEscrow proposalEscrow = new ProposalEscrow(
            address(reputationManager)
        );
        console.log("ProposalEscrow:", address(proposalEscrow));

        // 7. FreelanceEscrow
        FreelanceEscrow freelanceEscrow = new FreelanceEscrow(
            address(reputationManager),
            deployer
        );
        console.log("FreelanceEscrow:", address(freelanceEscrow));

        // 8. Wire up contracts
        reputationManager.setEscrowContract(address(queryEscrow));
        reputationManager.addAuthorizedCaller(address(proposalEscrow));
        reputationManager.addAuthorizedCaller(address(freelanceEscrow));
        console.log("ReputationManager: wired to QueryEscrow + ProposalEscrow + FreelanceEscrow");

        decisionLedger.setEscrowContract(address(queryEscrow));
        stakeVault.setRegistryContract(address(agentRegistry));

        queryEscrow.setOrchestratorAddress(deployer);
        proposalEscrow.setOrchestrator(deployer);
        // FreelanceEscrow orchestrator already set to deployer in constructor
        console.log("Orchestrator address set to deployer:", deployer);

        vm.stopBroadcast();

        // Save deployment summary
        string memory json = string(abi.encodePacked(
            "{\n",
            '  "network": "monad_testnet",\n',
            '  "chainId": 10143,\n',
            '  "deployer": "', vm.toString(deployer), '",\n',
            '  "contracts": {\n',
            '    "StakeVault": "', vm.toString(address(stakeVault)), '",\n',
            '    "ReputationManager": "', vm.toString(address(reputationManager)), '",\n',
            '    "DecisionLedger": "', vm.toString(address(decisionLedger)), '",\n',
            '    "AgentRegistry": "', vm.toString(address(agentRegistry)), '",\n',
            '    "QueryEscrow": "', vm.toString(address(queryEscrow)), '",\n',
            '    "ProposalEscrow": "', vm.toString(address(proposalEscrow)), '",\n',
            '    "FreelanceEscrow": "', vm.toString(address(freelanceEscrow)), '"\n',
            '  }\n',
            "}"
        ));

        console.log("\n=== Deployment Summary ===");
        console.log(json);
        vm.writeFile("deployments/monad_testnet.json", json);
        console.log("Saved to deployments/monad_testnet.json");
    }
}
